"""Datos de demostración para todo el ciclo de vida del proyecto (docs/flow.md).

Uso (con el servidor detenido o en paralelo, es seguro):
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf --no-http < scripts/demo_data.py

Idempotente: si ya existe el cliente "Corporación Andina (DEMO)" no hace nada.
Todos los registros llevan "(DEMO)" o "DEMO" en el nombre para identificarlos.
Requiere haber corrido antes la configuración base (plantilla de proyecto y productos).
"""
from datetime import date, datetime, timedelta

TODAY = date(2026, 11, 14)
ERRORS = []


def section(title):
    def deco(fn):
        print(f"\n== {title}")
        try:
            with env.cr.savepoint():
                fn()
        except Exception as e:  # noqa: BLE001 - report and continue with next section
            ERRORS.append((title, repr(e)))
            print(f"   !! ERROR en '{title}': {e!r}")
        return fn
    return deco


def ref(xmlid):
    return env.ref(xmlid)


def stage(model, name):
    rec = env[model].search([('name', '=', name)], limit=1)
    assert rec, f"No existe la etapa {name} en {model}"
    return rec


def dt(d, h=8):
    return datetime(d.year, d.month, d.day, h, 0)


if env['res.partner'].search_count([('name', '=', 'Corporación Andina (DEMO)')]):
    print("Los datos DEMO ya existen. Nada que hacer.")
    raise SystemExit(0)

admin = ref('base.user_admin')
tpl = env['project.project'].search([('is_template', '=', True), ('name', '=', 'Plantilla - Proyecto de obra')], limit=1)
assert tpl, "Falta la plantilla de proyecto: corre primero la configuración base."
P = lambda name: env['product.product'].search([('name', '=', name)], limit=1)  # noqa: E731
contract = P('Contrato de obra (por hitos)')
labor = P('Mano de obra por administración (horas)')
change_order = P('Change order')
materials_generic = P('Materiales de obra')
subcontract = P('Subcontrato')
assert contract and labor and change_order and subcontract, "Faltan productos de la configuración base."


S = {}  # objetos compartidos entre secciones


# ---------------------------------------------------------------------------
@section("Empleados con costo por hora")
def _employees():
    # (nombre, cargo, tarifa de pago/h). Con bizflow_labor el costo por hora = tarifa × factor cargado (1,40).
    team = [
        ('Carlos Mendoza (DEMO)', 'Gerente de proyecto', 9.00),
        ('Ana Torres (DEMO)', 'Estimadora', 7.00),
        ('Luis Pérez (DEMO)', 'Maestro de obra', 4.50),
        ('Jorge Ramírez (DEMO)', 'Albañil', 3.00),
        ('María Salazar (DEMO)', 'Electricista', 4.00),
        ('Diego Castillo (DEMO)', 'Ayudante', 2.60),
    ]
    has_labor = 'labor_pay_rate' in env['hr.employee']._fields
    emps = {}
    for name, job, rate in team:
        vals = {'name': name, 'job_title': job}
        vals.update({'labor_pay_rate': rate} if has_labor else {'hourly_cost': round(rate * 1.4, 2)})
        emps[name.split(' (')[0]] = env['hr.employee'].create(vals)
    if admin.employee_id:
        admin.employee_id.write({'labor_pay_rate': 12.0} if has_labor else {'hourly_cost': 16.8})

    # Usuarios internos para el gerente y el maestro de obra (registran horas de la cuadrilla).
    # Sin contraseña: el administrador la asigna desde Ajustes > Usuarios si se quiere probar con ellos.
    groups = [ref('base.group_user').id, ref('project.group_project_user').id,
              ref('hr_timesheet.group_hr_timesheet_approver').id]
    for key, login in [('Carlos Mendoza', 'carlos.mendoza@demo.local'), ('Luis Pérez', 'luis.perez@demo.local')]:
        user = env['res.users'].with_context(no_reset_password=True).create({
            'name': emps[key].name, 'login': login, 'group_ids': [(6, 0, groups)],
        })
        emps[key].user_id = user
    S['users'] = {k: emps[k].user_id for k in ('Carlos Mendoza', 'Luis Pérez')}
    S['emp'] = emps
    print("   ", ", ".join(f"{n} ${e.hourly_cost}/h" for n, e in emps.items()))


@section("Clientes y proveedores")
def _partners():
    customers = {
        'andina': ('Corporación Andina (DEMO)', 'Quito'),
        'pacifico': ('Inversiones Pacífico (DEMO)', 'Guayaquil'),
        'sierra': ('Distribuidora Sierra (DEMO)', 'Cuenca'),
        'costa': ('Hotel Costa Azul (DEMO)', 'Manta'),
        'norte': ('Clínica del Norte (DEMO)', 'Ibarra'),
        'austro': ('Colegio Austro (DEMO)', 'Loja'),
        'valle': ('Residencial El Valle (DEMO)', 'Quito'),
        'centro': ('Banco del Centro (DEMO)', 'Ambato'),
        'oriente': ('Petroservicios Oriente (DEMO)', 'Lago Agrio'),
    }
    S['cust'] = {k: env['res.partner'].create({'name': n, 'is_company': True, 'city': c, 'customer_rank': 1}) for k, (n, c) in customers.items()}
    vendors = {
        'ferre': 'Ferretería El Constructor (DEMO)',
        'cemento': 'Cementos y Agregados del Sur (DEMO)',
        'electro': 'Instalaciones Eléctricas Voltio (DEMO)',
        'pintura': 'Pinturas y Acabados Color (DEMO)',
    }
    S['vend'] = {k: env['res.partner'].create({'name': n, 'is_company': True, 'supplier_rank': 1}) for k, n in vendors.items()}
    print("   ", len(S['cust']), "clientes,", len(S['vend']), "proveedores")


@section("Productos de materiales con costo y proveedor")
def _products():
    unit = ref('uom.product_uom_unit')
    items = [
        ('Cemento portland saco 50kg (DEMO)', 9.5, 7.8, 'cemento'),
        ('Bloque de hormigón 15cm (DEMO)', 0.65, 0.48, 'cemento'),
        ('Varilla corrugada 12mm (DEMO)', 11.0, 8.9, 'ferre'),
        ('Pintura látex galón (DEMO)', 24.0, 17.5, 'pintura'),
        ('Cable eléctrico #12 rollo 100m (DEMO)', 68.0, 52.0, 'electro'),
    ]
    S['mat'] = {}
    for name, price, cost, vend in items:
        p = env['product.product'].create({
            'name': name, 'type': 'consu', 'sale_ok': True, 'purchase_ok': True,
            'list_price': price, 'standard_price': cost, 'uom_id': unit.id,
            'purchase_method': 'purchase',
            'seller_ids': [(0, 0, {'partner_id': S['vend'][vend].id, 'price': cost})],
        })
        S['mat'][name] = p
    subcontract.product_tmpl_id.purchase_method = 'purchase'
    print("   ", len(S['mat']), "materiales")


# ---------------------------------------------------------------------------
@section("CRM: leads y oportunidades en todas las etapas")
def _crm():
    c = S['cust']
    st = lambda n: stage('crm.stage', n)  # noqa: E731
    tag = lambda n: env['crm.tag'].search([('name', '=', n)], limit=1) or env['crm.tag'].create({'name': n})  # noqa: E731
    leads = [
        # (nombre, cliente, tipo, etapa, ingreso, prob, cierre, tags)
        ('Formulario web: cotización cerramiento', None, 'lead', None, 0, 10, None, ['Formulario web']),
        ('Llamada: remodelación de baños', None, 'lead', None, 0, 10, None, ['Llamada']),
        ('Referido: galpón industrial 800m2', None, 'lead', None, 0, 10, None, ['Referido']),
        ('Ampliación consultorios', c['norte'], 'opportunity', 'Nuevo', 85000, 10, 60, ['Referido']),
        ('Cubierta metálica cancha', c['austro'], 'opportunity', 'Nuevo', 32000, 10, 75, ['Marketing']),
        ('Urbanización etapa 2 - áreas comunales', c['valle'], 'opportunity', 'Calificado', 210000, 30, 45, ['Llamada']),
        ('Readecuación agencia bancaria', c['centro'], 'opportunity', 'Calificado', 54000, 35, 30, ['Formulario web']),
        ('Campamento modular', c['oriente'], 'opportunity', 'Estimando', 145000, 45, 25, ['Referido']),
    ]
    S['leads'] = {}
    for name, partner, typ, stg, rev, prob, close, tags in leads:
        vals = {'name': name, 'type': typ, 'user_id': admin.id, 'tag_ids': [(6, 0, [tag(t).id for t in tags])]}
        if partner:
            vals['partner_id'] = partner.id
        if stg:
            vals.update({'stage_id': st(stg).id, 'expected_revenue': rev, 'probability': prob,
                         'date_deadline': TODAY + timedelta(days=close)})
        if typ == 'lead':
            vals.update({'contact_name': 'Contacto ' + name.split(':')[0], 'email_from': 'contacto@example.com'})
        S['leads'][name] = env['crm.lead'].create(vals)

    # Oportunidades que llegan a presupuesto / se ganan / se pierden
    big = [
        ('k_andina', 'Remodelación oficinas corporativas', c['andina'], 'Ganado', 45000, 100, -100),
        ('k_pacifico', 'Construcción local comercial', c['pacifico'], 'Ganado', 120000, 100, -45),
        ('k_sierra', 'Ampliación bodega', c['sierra'], 'Ganado', 68000, 100, -4),
        ('k_costa', 'Mantenimiento integral hotel', c['costa'], 'Ganado', 9600, 100, -30),
        ('k_prop', 'Edificio de parqueaderos', c['valle'], 'Propuesta enviada', 380000, 60, 20),
        ('k_nego', 'Remodelación quirófanos', c['norte'], 'Negociación', 96000, 80, 10),
        ('k_lost', 'Bloque de aulas', c['austro'], 'Propuesta enviada', 150000, 50, -20),
    ]
    for key, name, partner, stg, rev, prob, close in big:
        S[key] = env['crm.lead'].create({
            'name': name, 'type': 'opportunity', 'partner_id': partner.id, 'user_id': admin.id,
            'stage_id': st('Estimando' if stg == 'Ganado' else stg).id,
            'expected_revenue': rev, 'probability': prob, 'date_deadline': TODAY + timedelta(days=close),
            'tag_ids': [(6, 0, [tag('Referido').id])],
        })
    print("   ", len(S['leads']) + len(big), "leads/oportunidades")


# ---------------------------------------------------------------------------
def make_so(lead, lines, order_date):
    so = env['sale.order'].create({
        'partner_id': lead.partner_id.id,
        'opportunity_id': lead.id,
        'sale_order_template_id': env['sale.order.template'].search([('name', '=', 'Estimado de obra')], limit=1).id,
        'date_order': dt(order_date),
        'require_signature': True,
        'order_line': [(5, 0, 0)] + [(0, 0, l) for l in lines],
    })
    return so


def confirm(so, d, name):
    so.action_confirm()
    so.date_order = dt(d)
    so.signed_by = so.partner_id.name
    so.signed_on = dt(d, 10)
    so.opportunity_id.action_set_won()
    project = so.project_ids[:1] or so.order_line.project_id[:1]
    project.write({'name': name})
    return project


def link_milestones(project, so_line, start, offsets, percents):
    ms = project.milestone_ids.sorted('id')
    for m, off, pct in zip(ms, offsets, percents):
        m.write({'sale_line_id': so_line.id, 'quantity_percentage': pct / 100.0, 'deadline': start + timedelta(days=off)})
    return ms


TASK_PLAN = {  # nombre: (inicio, fin) relativos a la duración total (0..1)
    'Kickoff con el cliente': (0.00, 0.03),
    'Schedule y cronograma': (0.03, 0.09),
    'Pedido de materiales y subcontratos': (0.09, 0.18),
    'Ejecución en campo': (0.18, 0.80),
    'Punch list': (0.80, 0.90),
    'Entrega final y documentos de cierre': (0.90, 0.96),
    'Lecciones aprendidas': (0.96, 1.00),
}


def plan_tasks(project, start, days, assignee):
    for t in project.task_ids:
        if t.name in TASK_PLAN:
            a, b = TASK_PLAN[t.name]
            t.write({
                'planned_date_start': dt(start + timedelta(days=round(a * days))),
                'planned_date_end': dt(start + timedelta(days=max(round(b * days), round(a * days) + 1)), 17),
                'date_deadline': dt(start + timedelta(days=max(round(b * days), round(a * days) + 1)), 17),
                'user_ids': [(6, 0, [assignee.id])],
            })
        elif t.name == 'Daily log':
            t.write({'date_deadline': dt(start, 17), 'user_ids': [(6, 0, [assignee.id])]})


def set_task(project, name, stage_name, done=False):
    t = project.task_ids.filtered(lambda x: x.name == name)
    vals = {'stage_id': stage('project.task.type', stage_name).id}
    if done:
        vals['state'] = '1_done'
    t.write(vals)
    return t


def timesheet(project, task_name, emp, d, hours, desc):
    task = project.task_ids.filtered(lambda x: x.name == task_name)[:1]
    env['account.analytic.line'].create({
        'name': desc, 'project_id': project.id, 'task_id': task.id,
        'employee_id': emp.id, 'unit_amount': hours, 'date': d,
    })


def purchase(vendor, project, lines, d, confirm_po=True, bill_date=None, pay_date=None):
    dist = {str(project.account_id.id): 100}
    po = env['purchase.order'].create({
        'partner_id': vendor.id, 'date_order': dt(d), 'origin': project.name,
        'order_line': [(0, 0, {'product_id': p.id, 'product_qty': q, 'price_unit': price, 'name': name or p.name,
                               'analytic_distribution': dist, 'date_planned': dt(d + timedelta(days=3))})
                       for p, q, price, name in lines],
    })
    if confirm_po:
        po.button_confirm()
        po.date_approve = dt(d)
    if bill_date:
        po.action_create_invoice()
        bill = po.invoice_ids[:1]
        bill.write({'invoice_date': bill_date, 'ref': f"FAC-{vendor.id}-{po.id:04d}"})
        bill.action_post()
        if pay_date:
            pay(bill, pay_date)
    return po


def pay(move, d):
    env['account.payment.register'].with_context(active_model='account.move', active_ids=move.ids).create(
        {'payment_date': d}).action_create_payments()


def downpayment(so, pct, d, pay_date=None):
    wiz = env['sale.advance.payment.inv'].with_context(active_model='sale.order', active_ids=so.ids).create(
        {'advance_payment_method': 'percentage', 'amount': pct})
    wiz.create_invoices()
    inv = so.invoice_ids.filtered(lambda m: m.state == 'draft')[:1]
    inv.invoice_date = d
    inv.action_post()
    if pay_date:
        pay(inv, pay_date)
    return inv


def invoice(so, d, pay_date=None):
    # final=True = casilla "Descontar anticipos" del asistente de facturación
    inv = so._create_invoices(final=True)
    inv.invoice_date = d
    inv.action_post()
    if pay_date:
        pay(inv, pay_date)
    return inv


def expense(emp, project, name, amount, d, approve=True):
    prod = env['product.product'].search([('can_be_expensed', '=', True)], limit=1)
    exp = env['hr.expense'].create({
        'name': name, 'employee_id': emp.id, 'product_id': prod.id, 'total_amount_currency': amount,
        'date': d, 'analytic_distribution': {str(project.account_id.id): 100}, 'payment_mode': 'own_account',
    })
    if approve:
        exp.action_submit()
        if exp.approval_state == 'submitted':
            exp.action_approve()
    return exp


def update(project, d, status, progress, title, body):
    env['project.update'].create({'project_id': project.id, 'date': d, 'status': status, 'progress': progress,
                                  'name': title, 'description': f"<p>{body}</p>"})


def daily_log(project, d, emp, text):
    tag = env['project.tags'].search([('name', '=', 'daily-log')], limit=1)
    env['project.task'].create({
        'name': f"Daily log {d.strftime('%d/%m/%Y')}", 'project_id': project.id, 'tag_ids': [(6, 0, tag.ids)],
        'stage_id': stage('project.task.type', 'Hecho').id, 'state': '1_done', 'user_ids': [(6, 0, [admin.id])],
        'description': f"<p><b>Encargado:</b> {emp.name}</p><p>{text}</p>",
        'planned_date_start': dt(d), 'planned_date_end': dt(d, 17),
    })


# ---------------------------------------------------------------------------
@section("Propuestas: enviada, en negociación y perdida")
def _proposals():
    so = make_so(S['k_prop'], [{'product_id': contract.id, 'product_uom_qty': 1, 'price_unit': 380000}], TODAY - timedelta(days=6))
    so.action_quotation_sent()
    so2 = make_so(S['k_nego'], [{'product_id': contract.id, 'product_uom_qty': 1, 'price_unit': 98500,
                                  'name': 'Remodelación quirófanos - versión 2 (revisada)'}], TODAY - timedelta(days=12))
    so2.action_quotation_sent()
    so3 = make_so(S['k_lost'], [{'product_id': contract.id, 'product_uom_qty': 1, 'price_unit': 150000}], TODAY - timedelta(days=40))
    so3.action_quotation_sent()
    so3._action_cancel()
    reason = env['crm.lost.reason'].search([], limit=1)
    S['k_lost'].action_set_lost(lost_reason_id=reason.id)
    print("   ", so.name, "enviada |", so2.name, "negociación |", so3.name, "perdida:", reason.name)


# ---------------------------------------------------------------------------
@section("Proyecto 1 (CERRADO): Remodelación oficinas Corporación Andina")
def _p1():
    e = S['emp']
    start = date(2026, 8, 3)
    so = make_so(S['k_andina'], [
        {'product_id': contract.id, 'product_uom_qty': 1, 'price_unit': 45000, 'name': 'Remodelación oficinas corporativas 3er piso - 420 m2'},
    ], date(2026, 7, 20))
    pr = confirm(so, date(2026, 7, 28), 'Remodelación oficinas - Corporación Andina (DEMO)')
    pr.write({'date_start': start, 'date': date(2026, 10, 9), 'user_id': admin.id,
              'stage_id': stage('project.project.stage', 'Cerrado').id})
    cline = so.order_line.filtered(lambda l: l.product_id == contract)
    ms = link_milestones(pr, cline, start, [7, 35, 67], [30, 40, 30])
    plan_tasks(pr, start, 67, admin)
    for t in pr.task_ids:
        t.write({'stage_id': stage('project.task.type', 'Hecho').id, 'state': '1_done'})

    # Horas
    for i in range(0, 60, 3):
        d = start + timedelta(days=i)
        timesheet(pr, 'Ejecución en campo', e['Luis Pérez'], d, 8, 'Supervisión de obra')
        timesheet(pr, 'Ejecución en campo', e['Jorge Ramírez'], d, 8, 'Mampostería y enlucidos')
        if i % 6 == 0:
            timesheet(pr, 'Ejecución en campo', e['María Salazar'], d, 6, 'Instalaciones eléctricas')
    timesheet(pr, 'Kickoff con el cliente', e['Carlos Mendoza'], start, 3, 'Reunión de arranque')
    timesheet(pr, 'Schedule y cronograma', e['Carlos Mendoza'], start + timedelta(days=1), 6, 'Cronograma y recursos')
    timesheet(pr, 'Punch list', e['Luis Pérez'], date(2026, 10, 1), 8, 'Corrección de observaciones')
    timesheet(pr, 'Lecciones aprendidas', e['Carlos Mendoza'], date(2026, 10, 9), 2, 'Retrospectiva')

    # Compras
    m = S['mat']; v = S['vend']
    purchase(v['cemento'], pr, [(m['Cemento portland saco 50kg (DEMO)'], 180, 7.8, None), (m['Bloque de hormigón 15cm (DEMO)'], 2500, 0.48, None)],
             date(2026, 8, 5), bill_date=date(2026, 8, 8), pay_date=date(2026, 9, 7))
    purchase(v['pintura'], pr, [(m['Pintura látex galón (DEMO)'], 60, 17.5, None)], date(2026, 9, 10),
             bill_date=date(2026, 9, 12), pay_date=date(2026, 10, 12))
    purchase(v['electro'], pr, [(subcontract, 1, 6200, 'Subcontrato instalaciones eléctricas y datos')], date(2026, 8, 20),
             bill_date=date(2026, 9, 25), pay_date=date(2026, 10, 20))
    expense(e['Luis Pérez'], pr, 'Transporte de escombros', 180, date(2026, 9, 3))
    expense(e['Carlos Mendoza'], pr, 'Movilización a obra', 65, date(2026, 8, 4))

    # Change order
    tag_co = env['project.tags'].search([('name', '=', 'change-order')], limit=1)
    co = env['sale.order.line'].create({'order_id': so.id, 'product_id': change_order.id, 'price_unit': 3500,
                                        'name': 'CO-01: Cielo raso acústico sala de reuniones'})
    co.task_id.write({'tag_ids': [(6, 0, tag_co.ids)], 'stage_id': stage('project.task.type', 'Hecho').id, 'state': '1_done'})
    co.qty_delivered = 1

    # Facturación: anticipo + 3 hitos, todo cobrado
    downpayment(so, 20, date(2026, 7, 29), pay_date=date(2026, 8, 2))
    ms[0].is_reached = True
    invoice(so, date(2026, 8, 12), pay_date=date(2026, 8, 28))
    ms[1].is_reached = True
    invoice(so, date(2026, 9, 8), pay_date=date(2026, 9, 30))
    ms[2].is_reached = True
    invoice(so, date(2026, 10, 9), pay_date=date(2026, 10, 30))

    # Punch list y cierre
    tag_pl = env['project.tags'].search([('name', '=', 'punch-list')], limit=1)
    parent = pr.task_ids.filtered(lambda t: t.name == 'Punch list')
    for item in ['Retocar pintura pasillo', 'Ajustar puerta baño 2', 'Reemplazar luminaria sala B']:
        env['project.task'].create({'name': item, 'project_id': pr.id, 'parent_id': parent.id, 'tag_ids': [(6, 0, tag_pl.ids)],
                                    'stage_id': stage('project.task.type', 'Hecho').id, 'state': '1_done'})
    pr.task_ids.filtered(lambda t: t.name == 'Lecciones aprendidas').description = (
        "<ul><li>El pedido anticipado de bloque evitó 4 días de retraso.</li>"
        "<li>El subcontrato eléctrico se pasó 1 semana: pedir cronograma detallado.</li>"
        "<li>Margen final por encima del estimado gracias al change order.</li></ul>")
    update(pr, date(2026, 8, 31), 'on_track', 35, 'Avance agosto', 'Obra gris al 90%. Sin novedades.')
    update(pr, date(2026, 9, 30), 'at_risk', 80, 'Avance septiembre', 'Retraso de 1 semana en eléctricas. Se reforzó cuadrilla.')
    update(pr, date(2026, 10, 9), 'done', 100, 'Cierre', 'Entrega aceptada por el cliente. Acta firmada.')
    S['p1'] = pr
    print("   ", pr.name, "| SO", so.name, "| facturas:", len(so.invoice_ids))


# ---------------------------------------------------------------------------
@section("Proyecto 2 (EJECUCIÓN): Local comercial Inversiones Pacífico")
def _p2():
    e = S['emp']
    start = date(2026, 10, 5)
    so = make_so(S['k_pacifico'], [
        {'product_id': contract.id, 'product_uom_qty': 1, 'price_unit': 120000, 'name': 'Construcción local comercial 650 m2 - llave en mano'},
    ], date(2026, 9, 18))
    pr = confirm(so, date(2026, 9, 29), 'Local comercial - Inversiones Pacífico (DEMO)')
    pr.write({'date_start': start, 'date': date(2027, 1, 29), 'user_id': admin.id,
              'stage_id': stage('project.project.stage', 'Ejecución').id})
    cline = so.order_line.filtered(lambda l: l.product_id == contract)
    ms = link_milestones(pr, cline, start, [10, 60, 116], [30, 40, 30])
    plan_tasks(pr, start, 116, admin)
    for n in ['Kickoff con el cliente', 'Schedule y cronograma', 'Pedido de materiales y subcontratos']:
        set_task(pr, n, 'Hecho', done=True)
    set_task(pr, 'Ejecución en campo', 'En progreso')

    # Work orders
    tag_wo = env['project.tags'].search([('name', '=', 'work-order')], limit=1)
    parent = pr.task_ids.filtered(lambda t: t.name == 'Ejecución en campo')
    wos = [('WO-01 Cimentación', 'Hecho', True, 0, 14), ('WO-02 Estructura metálica', 'Hecho', True, 14, 35),
           ('WO-03 Mampostería', 'En progreso', False, 35, 55), ('WO-04 Instalaciones eléctricas', 'Por hacer', False, 50, 70),
           ('WO-05 Acabados y pintura', 'Por hacer', False, 70, 90)]
    for name, stg, done, a, b in wos:
        vals = {'name': name, 'project_id': pr.id, 'parent_id': parent.id, 'tag_ids': [(6, 0, tag_wo.ids)],
                'stage_id': stage('project.task.type', stg).id, 'user_ids': [(6, 0, [admin.id])],
                'planned_date_start': dt(start + timedelta(days=a + 21)), 'planned_date_end': dt(start + timedelta(days=b + 21), 17)}
        if done:
            vals['state'] = '1_done'
        env['project.task'].create(vals)
    blocked = pr.task_ids.filtered(lambda t: t.name == 'WO-04 Instalaciones eléctricas')
    blocked.write({'state': '02_changes_requested'}) if 'state' in blocked._fields else None

    # Horas (octubre - noviembre)
    d = start
    while d <= TODAY:
        if d.weekday() < 5:
            timesheet(pr, 'Ejecución en campo', e['Luis Pérez'], d, 8, 'Supervisión de obra')
            timesheet(pr, 'Ejecución en campo', e['Jorge Ramírez'], d, 8, 'Trabajos de obra civil')
            timesheet(pr, 'Ejecución en campo', e['Diego Castillo'], d, 8, 'Ayudante de obra')
            if d.weekday() in (1, 3) and d >= date(2026, 10, 19):  # martes y jueves: 2 h extra del maestro
                timesheet(pr, 'Ejecución en campo', e['Luis Pérez'], d, 2, 'Fundición de losa (horas extra)')
        elif d.weekday() == 5 and d >= date(2026, 10, 24):  # sábados medio día
            timesheet(pr, 'Ejecución en campo', e['Jorge Ramírez'], d, 4, 'Avance sábado')
            timesheet(pr, 'Ejecución en campo', e['Diego Castillo'], d, 4, 'Avance sábado')
        d += timedelta(days=1)
    timesheet(pr, 'Kickoff con el cliente', e['Carlos Mendoza'], start, 4, 'Kickoff')
    timesheet(pr, 'Schedule y cronograma', e['Carlos Mendoza'], start + timedelta(days=2), 10, 'Cronograma maestro')
    timesheet(pr, 'Pedido de materiales y subcontratos', e['Ana Torres'], start + timedelta(days=5), 12, 'Cotizaciones y pedidos')

    # Daily logs
    logs = [
        (date(2026, 11, 10), 'Clima soleado. 6 personas en obra. Avance mampostería eje A-C.'),
        (date(2026, 11, 11), 'Lluvia en la tarde, se suspendió 2h. Llegó pedido de bloque.'),
        (date(2026, 11, 12), 'Mampostería eje D. Inspección de fiscalización sin observaciones.'),
        (date(2026, 11, 13), 'Falta cable #12: proveedor confirma entrega el lunes. WO-04 en espera.'),
    ]
    for d, text in logs:
        daily_log(pr, d, e['Luis Pérez'], text)

    # Compras: pagadas, por pagar y en cotización
    m = S['mat']; v = S['vend']
    purchase(v['cemento'], pr, [(m['Cemento portland saco 50kg (DEMO)'], 600, 7.8, None), (m['Varilla corrugada 12mm (DEMO)'], 400, 8.9, None)],
             date(2026, 10, 8), bill_date=date(2026, 10, 10), pay_date=date(2026, 11, 9))
    purchase(v['ferre'], pr, [(subcontract, 1, 28000, 'Subcontrato estructura metálica')], date(2026, 10, 15),
             bill_date=date(2026, 11, 5))
    purchase(v['cemento'], pr, [(m['Bloque de hormigón 15cm (DEMO)'], 9000, 0.48, None)], date(2026, 11, 3))
    purchase(v['electro'], pr, [(m['Cable eléctrico #12 rollo 100m (DEMO)'], 25, 52.0, None),
                                (subcontract, 1, 9500, 'Subcontrato instalaciones eléctricas')], date(2026, 11, 12), confirm_po=False)
    expense(e['Luis Pérez'], pr, 'Alquiler de andamios (semana)', 420, date(2026, 11, 2))
    expense(e['Carlos Mendoza'], pr, 'Permiso municipal de construcción', 850, date(2026, 10, 6))
    expense(e['Diego Castillo'], pr, 'Combustible generador', 75, date(2026, 11, 12), approve=False)

    # Change order aprobado, aún no facturado
    tag_co = env['project.tags'].search([('name', '=', 'change-order')], limit=1)
    co = env['sale.order.line'].create({'order_id': so.id, 'product_id': change_order.id, 'price_unit': 7800,
                                        'name': 'CO-01: Mezzanine adicional 40 m2'})
    co.task_id.write({'tag_ids': [(6, 0, tag_co.ids)], 'stage_id': stage('project.task.type', 'En progreso').id})

    # Facturación: anticipo cobrado, hito 1 facturado y pendiente de cobro
    downpayment(so, 20, date(2026, 9, 30), pay_date=date(2026, 10, 3))
    ms[0].is_reached = True
    invoice(so, date(2026, 10, 20))
    update(pr, date(2026, 10, 31), 'on_track', 25, 'Avance octubre', 'Cimentación y estructura metálica terminadas.')
    update(pr, date(2026, 11, 13), 'at_risk', 40, 'Avance semana 6',
           'Riesgo: retraso en cable eléctrico (WO-04). Change order CO-01 aprobado por el cliente.')
    S['p2'] = pr
    print("   ", pr.name, "| SO", so.name, "| facturas:", len(so.invoice_ids))


# ---------------------------------------------------------------------------
@section("Proyecto 3 (PLANIFICACIÓN): Ampliación bodega Distribuidora Sierra")
def _p3():
    e = S['emp']
    start = date(2026, 11, 23)
    so = make_so(S['k_sierra'], [
        {'product_id': contract.id, 'product_uom_qty': 1, 'price_unit': 68000, 'name': 'Ampliación bodega 500 m2 - estructura y cubierta'},
    ], date(2026, 11, 2))
    pr = confirm(so, date(2026, 11, 10), 'Ampliación bodega - Distribuidora Sierra (DEMO)')
    pr.write({'date_start': start, 'date': date(2027, 2, 26), 'user_id': admin.id,
              'stage_id': stage('project.project.stage', 'Planificación').id})
    cline = so.order_line.filtered(lambda l: l.product_id == contract)
    link_milestones(pr, cline, start, [10, 50, 95], [30, 40, 30])
    plan_tasks(pr, start, 95, admin)
    set_task(pr, 'Schedule y cronograma', 'En progreso')
    timesheet(pr, 'Schedule y cronograma', e['Carlos Mendoza'], date(2026, 11, 12), 5, 'Borrador de cronograma')
    timesheet(pr, 'Schedule y cronograma', e['Ana Torres'], date(2026, 11, 13), 4, 'Revisión de cantidades')
    purchase(S['vend']['ferre'], pr, [(S['mat']['Varilla corrugada 12mm (DEMO)'], 350, 8.9, None),
                                      (subcontract, 1, 18500, 'Subcontrato cubierta metálica')], date(2026, 11, 13), confirm_po=False)
    downpayment(so, 20, date(2026, 11, 11))
    update(pr, date(2026, 11, 13), 'on_track', 5, 'Planificación', 'Anticipo (20%) facturado, esperando pago para iniciar el 23/11.')
    S['p3'] = pr
    print("   ", pr.name, "| SO", so.name)


# ---------------------------------------------------------------------------
@section("Proyecto 4 (POR ADMINISTRACIÓN): Mantenimiento Hotel Costa Azul")
def _p4():
    e = S['emp']
    start = date(2026, 10, 15)
    so = make_so(S['k_costa'], [
        {'product_id': labor.id, 'product_uom_qty': 400, 'price_unit': 24, 'name': 'Mano de obra mantenimiento (bolsa 400 h)'},
    ], date(2026, 10, 8))
    pr = confirm(so, date(2026, 10, 13), 'Mantenimiento integral - Hotel Costa Azul (DEMO)')
    pr.write({'date_start': start, 'date': date(2026, 12, 31), 'user_id': admin.id,
              'stage_id': stage('project.project.stage', 'Ejecución').id})
    task = so.order_line.task_id[:1]
    task.write({'name': 'Mantenimiento habitaciones y áreas comunes', 'stage_id': stage('project.task.type', 'En progreso').id,
                'planned_date_start': dt(start), 'planned_date_end': dt(date(2026, 12, 31), 17)})
    d = start
    while d <= TODAY:
        if d.weekday() < 5:
            env['account.analytic.line'].create({'name': 'Mantenimiento correctivo', 'project_id': pr.id, 'task_id': task.id,
                                                 'employee_id': e['María Salazar'].id, 'unit_amount': 6, 'date': d,
                                                 'so_line': so.order_line[:1].id})
            env['account.analytic.line'].create({'name': 'Pintura habitaciones', 'project_id': pr.id, 'task_id': task.id,
                                                 'employee_id': e['Diego Castillo'].id, 'unit_amount': 6, 'date': d,
                                                 'so_line': so.order_line[:1].id})
        d += timedelta(days=1)
    invoice(so, date(2026, 10, 31), pay_date=date(2026, 11, 12))
    purchase(S['vend']['pintura'], pr, [(S['mat']['Pintura látex galón (DEMO)'], 40, 17.5, None)], date(2026, 10, 16),
             bill_date=date(2026, 10, 18), pay_date=date(2026, 11, 10))
    update(pr, date(2026, 11, 13), 'on_track', 55, 'Avance noviembre', 'Habitaciones piso 1-3 listas. Facturado octubre.')
    S['p4'] = pr
    print("   ", pr.name, "| SO", so.name, "| horas:", sum(pr.timesheet_ids.mapped('unit_amount')))


# ---------------------------------------------------------------------------
@section("Equipos de obra por proyecto")
def _teams():
    if 'labor_employee_ids' not in env['project.project']._fields:
        print("    (bizflow_labor no instalado, se omite)")
        return
    e = S['emp']
    teams = {
        'p1': ['Luis Pérez', 'Jorge Ramírez', 'María Salazar', 'Carlos Mendoza'],
        'p2': ['Luis Pérez', 'Jorge Ramírez', 'Diego Castillo', 'María Salazar', 'Carlos Mendoza', 'Ana Torres'],
        'p3': ['Carlos Mendoza', 'Ana Torres'],
        'p4': ['María Salazar', 'Diego Castillo'],
    }
    for key, names in teams.items():
        S[key].write({'labor_employee_ids': [(6, 0, [e[n].id for n in names])], 'labor_restrict_team': True,
                      'user_id': S['users']['Carlos Mendoza'].id})
    print("   ", {S[k].name: len(v) for k, v in teams.items()})


@section("Liquidaciones semanales de mano de obra")
def _labor_periods():
    if 'bizflow.labor.period' not in env:
        print("    (bizflow_labor no instalado, se omite)")
        return
    Period = env['bizflow.labor.period']
    company = env.company
    first = env['account.analytic.line'].search([('project_id', '!=', False)], order='date', limit=1).date
    monday = first - timedelta(days=first.weekday())
    last_monday = TODAY - timedelta(days=TODAY.weekday())  # semana en curso (9-15 nov)
    created = []
    while monday <= last_monday:
        period = Period.create({'date_from': monday, 'date_to': monday + timedelta(days=6)})
        period.action_generate()
        if not period.line_ids:
            period.unlink()
            monday += timedelta(days=7)
            continue
        if monday < last_monday:  # semanas cerradas: aprobar y contabilizar
            period.action_approve()
            period.action_post()
            if monday < last_monday - timedelta(days=7):  # pagadas el viernes siguiente
                pay_date = period.date_to + timedelta(days=5)
                env['bizflow.labor.pay.wizard'].create({
                    'period_id': period.id, 'journal_id': company.labor_payment_journal_id.id,
                    'payment_date': pay_date}).action_pay()
        created.append(period)
        monday += timedelta(days=7)
    env['project.task']._cron_labor_hours_alert()
    by_state = {}
    for p in created:
        by_state[p.state] = by_state.get(p.state, 0) + 1
    print("   ", len(created), "periodos:", by_state,
          "| pagado total $%.2f" % sum(p.amount_total for p in created if p.state == 'paid'),
          "| por pagar $%.2f" % sum(p.amount_total for p in created if p.state == 'posted'))


if ERRORS:
    print("\n!! Secciones con errores (se revirtieron sin afectar al resto):")
    for t, e in ERRORS:
        print("   -", t, ":", e[:300])
env.cr.commit()
print("\nOK - datos DEMO guardados")
for k in ('p1', 'p2', 'p3', 'p4'):
    if k in S:
        pr = S[k]
        print(f"   {pr.name}: etapa={pr.stage_id.name}, tareas={len(pr.with_context(active_test=False).task_ids)}, "
              f"horas={sum(pr.timesheet_ids.mapped('unit_amount')):.0f}")
