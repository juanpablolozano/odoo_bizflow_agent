"""Agrega el demo de mano de obra (bizflow_labor) a una base que YA tiene los datos DEMO.

Uso:
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf --no-http < scripts/demo_labor.py

Para una base nueva no hace falta: scripts/setup_db.sh --demo ya incluye todo.
Idempotente: si ya hay liquidaciones, no hace nada.
"""
from datetime import date, timedelta

TODAY = date(2026, 11, 14)

assert 'bizflow.labor.period' in env, "Instale primero el módulo bizflow_labor"
if env['bizflow.labor.period'].search_count([]):
    print("Ya existen liquidaciones. Nada que hacer.")
    raise SystemExit(0)

Emp = env['hr.employee']
E = {n: Emp.search([('name', '=', f'{n} (DEMO)')], limit=1) for n in
     ['Carlos Mendoza', 'Ana Torres', 'Luis Pérez', 'Jorge Ramírez', 'María Salazar', 'Diego Castillo']}
assert all(E.values()), "Faltan los empleados DEMO: corra primero scripts/demo_data.py"
P = {k: env['project.project'].search([('name', 'ilike', n), ('name', 'ilike', '(DEMO)')], limit=1) for k, n in
     [('p1', 'Corporación Andina'), ('p2', 'Inversiones Pacífico'), ('p3', 'Distribuidora Sierra'), ('p4', 'Hotel Costa Azul')]}
ref = env.ref
admin = ref('base.user_admin')

# 1. Tarifas de pago (costo por hora = tarifa × 1,40)
rates = {'Carlos Mendoza': 9.0, 'Ana Torres': 7.0, 'Luis Pérez': 4.5, 'Jorge Ramírez': 3.0,
         'María Salazar': 4.0, 'Diego Castillo': 2.6}
for n, r in rates.items():
    E[n].labor_pay_rate = r
if admin.employee_id:
    admin.employee_id.labor_pay_rate = 12.0
print("Tarifas:", {n: f"${r}/h → costo ${E[n].hourly_cost}/h" for n, r in rates.items()})

# 2. Recalcular el costo de los partes de horas existentes con el nuevo costo cargado
lines = env['account.analytic.line'].search([('project_id', '!=', False), ('employee_id', 'in', [e.id for e in E.values()])])
for ts in lines:
    ts.amount = -ts.unit_amount * ts.employee_id.hourly_cost
print("Costo recalculado en", len(lines), "partes de horas")

# 3. Usuarios para gerente y maestro de obra
groups = [ref('base.group_user').id, ref('project.group_project_user').id,
          ref('hr_timesheet.group_hr_timesheet_approver').id]
for key, login in [('Carlos Mendoza', 'carlos.mendoza@demo.local'), ('Luis Pérez', 'luis.perez@demo.local')]:
    if not E[key].user_id:
        user = env['res.users'].search([('login', '=', login)], limit=1) or env['res.users'].with_context(
            no_reset_password=True).create({'name': E[key].name, 'login': login, 'group_ids': [(6, 0, groups)]})
        E[key].user_id = user
print("Usuarios:", [E[k].user_id.login for k in ('Carlos Mendoza', 'Luis Pérez')])

# 4. Equipos de obra
teams = {
    'p1': ['Luis Pérez', 'Jorge Ramírez', 'María Salazar', 'Carlos Mendoza'],
    'p2': ['Luis Pérez', 'Jorge Ramírez', 'Diego Castillo', 'María Salazar', 'Carlos Mendoza', 'Ana Torres'],
    'p3': ['Carlos Mendoza', 'Ana Torres', 'Diego Castillo'],
    'p4': ['María Salazar', 'Diego Castillo'],
}
for k, names in teams.items():
    P[k].write({'labor_employee_ids': [(6, 0, [E[n].id for n in names])], 'labor_restrict_team': True,
                'user_id': E['Carlos Mendoza'].user_id.id})
print("Equipos:", {P[k].name: len(v) for k, v in teams.items()})

# 5. Horas extra y sábados en el local comercial (para que haya recargos)
p2 = P['p2']
task = p2.task_ids.filtered(lambda t: t.name == 'Ejecución en campo')[:1]
new = []
d = date(2026, 10, 19)
while d <= TODAY:
    if d.weekday() in (1, 3):
        new.append({'name': 'Fundición de losa (horas extra)', 'employee_id': E['Luis Pérez'].id, 'unit_amount': 2, 'date': d})
    if d.weekday() == 5 and d >= date(2026, 10, 24):
        for n in ('Jorge Ramírez', 'Diego Castillo'):
            new.append({'name': 'Avance sábado', 'employee_id': E[n].id, 'unit_amount': 4, 'date': d})
    d += timedelta(days=1)
env['account.analytic.line'].create([dict(v, project_id=p2.id, task_id=task.id) for v in new])
print("Horas extra/sábado agregadas:", len(new))

# 6. Liquidaciones semanales: pagadas, por pagar (semana pasada) y en borrador (semana actual)
Period = env['bizflow.labor.period']
company = env.company
first = env['account.analytic.line'].search([('project_id', '!=', False)], order='date', limit=1).date
monday = first - timedelta(days=first.weekday())
last_monday = TODAY - timedelta(days=TODAY.weekday())
created = Period.browse()
while monday <= last_monday:
    period = Period.create({'date_from': monday, 'date_to': monday + timedelta(days=6)})
    period.action_generate()
    if not period.line_ids:
        period.unlink()
    else:
        if monday < last_monday:
            period.action_approve()
            period.action_post()
            if monday < last_monday - timedelta(days=7):
                env['bizflow.labor.pay.wizard'].create({
                    'period_id': period.id, 'journal_id': company.labor_payment_journal_id.id,
                    'payment_date': period.date_to + timedelta(days=5)}).action_pay()
        created |= period
    monday += timedelta(days=7)
env['project.task']._cron_labor_hours_alert()
env.cr.commit()
by_state = {}
for p in created:
    by_state[p.state] = by_state.get(p.state, 0) + 1
print(f"Liquidaciones: {len(created)} {by_state} | pagado ${sum(created.filtered(lambda p: p.state == 'paid').mapped('amount_total')):,.2f}"
      f" | por pagar ${sum(created.filtered(lambda p: p.state == 'posted').mapped('amount_total')):,.2f}"
      f" | borrador ${sum(created.filtered(lambda p: p.state == 'draft').mapped('amount_total')):,.2f}")
print("OK")
