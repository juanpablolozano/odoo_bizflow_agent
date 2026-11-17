"""Configuración base de BizFlow para gestión de proyectos (Odoo 20 Community).

Uso:
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf --no-http < scripts/configure_base.py

Idempotente: busca antes de crear. Requiere los módulos de scripts/setup_db.sh.
"""
from datetime import datetime, timedelta

def get_or_create(model, domain, vals):
    rec = env[model].with_context(active_test=False).search(domain, limit=1)
    if rec:
        rec.write(vals)
        if 'active' in rec._fields and not rec.active:
            rec.active = True
        return rec
    return env[model].create(vals)

# --- 0. Idioma español ---------------------------------------------------
if not env['res.lang']._lang_get('es_419'):
    env['base.language.install'].create({'lang_ids': [(6, 0, [env.ref('base.lang_es_419').id])], 'overwrite': False}).lang_install()
env.ref('base.lang_es_419').active = True
env.ref('base.user_admin').lang = 'es_419'
print('Idioma es_419 activo')

# --- 1. Ajustes generales --------------------------------------------------
env['res.config.settings'].create({
    'group_project_stages': True,
    'group_analytic_accounting': True,
    'portal_confirmation_sign': True,
}).execute()
print('Ajustes: etapas de proyecto, analítica, firma en línea')

# --- 2. Etapas del proyecto (ciclo de vida) --------------------------------
Stage = env['project.project.stage'].with_context(active_test=False)
wanted = [('Planificación', False), ('Ejecución', False), ('Punch list', False), ('Cierre', False), ('Cerrado', True)]
keep = Stage.browse()
for seq, (name, fold) in enumerate(wanted, start=1):
    keep |= get_or_create('project.project.stage', [('name', '=', name)], {'name': name, 'sequence': seq, 'fold': fold})
others = Stage.search([('id', 'not in', keep.ids), ('active', '=', True)])
others.write({'active': False})
print('Etapas de proyecto:', keep.mapped('name'), '| archivadas:', others.mapped('name'))

# --- 3. Etapas del CRM -----------------------------------------------------
crm = [('Nuevo', False), ('Calificado', False), ('Estimando', False), ('Propuesta enviada', False), ('Negociación', False), ('Ganado', True)]
old = env['crm.stage'].search([])
rename = {'New': 'Nuevo', 'Qualified': 'Calificado', 'Proposition': 'Propuesta enviada', 'Won': 'Ganado'}
for st in old:
    en = st.with_context(lang='en_US').name
    if en in rename:
        st.with_context(lang='es_419').name = rename[en]
        st.with_context(lang='en_US').name = rename[en]
for seq, (name, won) in enumerate(crm, start=1):
    get_or_create('crm.stage', [('name', '=', name)], {'name': name, 'sequence': seq, 'is_won': won})
print('Etapas CRM:', env['crm.stage'].search([]).mapped('name'))

# --- 4. Etiquetas de tareas ------------------------------------------------
tags = {}
for name in ['work-order', 'daily-log', 'change-order', 'punch-list']:
    tags[name] = get_or_create('project.tags', [('name', '=', name)], {'name': name})
print('Etiquetas:', list(tags))

# --- 5. Etapas de tareas + plantilla de proyecto ---------------------------
task_stages = env['project.task.type']
for seq, (name, fold) in enumerate([('Por hacer', False), ('En progreso', False), ('Revisión', False), ('Hecho', True)], start=1):
    task_stages |= get_or_create('project.task.type', [('name', '=', name), ('user_id', '=', False)], {'name': name, 'sequence': seq, 'fold': fold})

tpl = get_or_create('project.project', [('name', '=', 'Plantilla - Proyecto de obra'), ('is_template', '=', True)], {
    'name': 'Plantilla - Proyecto de obra',
    'is_template': True,
    'allow_milestones': True,
    'allow_task_dependencies': True,
    'allow_recurring_tasks': True,
    'allow_timesheets': True,
    'allow_billable': True,
    'privacy_visibility': 'followers',
    'type_ids': [(6, 0, task_stages.ids)],
    'tag_ids': [(6, 0, [])],
})

milestones = {}
for name in ['Anticipo / Inicio de obra', 'Avance 50%', 'Entrega final']:
    milestones[name] = get_or_create('project.milestone', [('name', '=', name), ('project_id', '=', tpl.id)], {'name': name, 'project_id': tpl.id})

task_defs = [
    ('Kickoff con el cliente', [], 'Anticipo / Inicio de obra', 2),
    ('Schedule y cronograma', [], 'Anticipo / Inicio de obra', 4),
    ('Pedido de materiales y subcontratos', [], 'Anticipo / Inicio de obra', 4),
    ('Ejecución en campo', ['work-order'], 'Avance 50%', 40),
    ('Daily log', ['daily-log'], False, 0.5),
    ('Punch list', ['punch-list'], 'Entrega final', 8),
    ('Entrega final y documentos de cierre', [], 'Entrega final', 4),
    ('Lecciones aprendidas', [], False, 2),
]
prev = env['project.task']
for seq, (name, tnames, ms, hours) in enumerate(task_defs, start=1):
    vals = {
        'name': name, 'project_id': tpl.id, 'sequence': seq,
        'stage_id': task_stages[0].id,
        'tag_ids': [(6, 0, [tags[t].id for t in tnames])],
        'milestone_id': milestones[ms].id if ms else False,
        'allocated_hours': hours,
    }
    if name == 'Daily log':
        vals.update({'recurring_task': True, 'repeat_interval': 1, 'repeat_unit': 'day', 'repeat_type': 'forever'})
    task = get_or_create('project.task', [('name', '=', name), ('project_id', '=', tpl.id)], vals)
    if prev and name not in ('Daily log', 'Lecciones aprendidas'):
        task.depend_on_ids = [(6, 0, prev.ids)]
    if name not in ('Daily log', 'Lecciones aprendidas'):
        prev = task
# Fechas planificadas de referencia (los proyectos nuevos las copian; ajustarlas por obra)
plan = {'Kickoff con el cliente': (0, 1), 'Schedule y cronograma': (1, 3), 'Pedido de materiales y subcontratos': (3, 6),
        'Ejecución en campo': (6, 26), 'Punch list': (26, 29), 'Entrega final y documentos de cierre': (29, 31),
        'Lecciones aprendidas': (31, 32)}
base = datetime(2026, 11, 16, 8, 0)
if 'planned_date_start' in env['project.task']._fields:
    for t in tpl.task_ids:
        if t.name in plan and not t.planned_date_start:
            a, b = plan[t.name]
            t.write({'planned_date_start': base + timedelta(days=a), 'planned_date_end': base + timedelta(days=b)})
print('Plantilla de proyecto:', tpl.name, '| tareas:', len(tpl.task_ids), '| hitos:', len(tpl.milestone_ids))

# --- 6. Productos ----------------------------------------------------------
hour = env.ref('uom.product_uom_hour')
unit = env.ref('uom.product_uom_unit')
products = [
    ('Contrato de obra (por hitos)', {
        'type': 'service', 'sale_ok': True, 'purchase_ok': False,
        'service_tracking': 'project_only', 'project_template_id': tpl.id,
        'service_policy': 'delivered_milestones', 'uom_id': unit.id,
    }),
    ('Mano de obra por administración (horas)', {
        'type': 'service', 'sale_ok': True, 'purchase_ok': False,
        'service_tracking': 'task_in_project', 'project_template_id': tpl.id,
        'service_policy': 'delivered_timesheet', 'uom_id': hour.id,
    }),
    ('Change order', {
        'type': 'service', 'sale_ok': True, 'purchase_ok': False,
        'service_tracking': 'task_in_project', 'project_template_id': tpl.id,
        'service_policy': 'delivered_manual', 'uom_id': unit.id,
    }),
    ('Materiales de obra', {'type': 'consu', 'sale_ok': True, 'purchase_ok': True, 'uom_id': unit.id}),
    ('Subcontrato', {'type': 'service', 'sale_ok': False, 'purchase_ok': True, 'uom_id': unit.id}),
]
for name, vals in products:
    vals = dict(vals, name=name)
    get_or_create('product.template', [('name', '=', name)], vals)
print('Productos:', [p[0] for p in products])

# --- 7. Plantilla de presupuesto (estimado) --------------------------------
if 'sale.order.template' in env:
    tmpl = get_or_create('sale.order.template', [('name', '=', 'Estimado de obra')], {'name': 'Estimado de obra', 'require_signature': True})
    if not tmpl.sale_order_template_line_ids:
        lines = []
        for pname in ['Contrato de obra (por hitos)', 'Materiales de obra']:
            p = env['product.product'].search([('name', '=', pname)], limit=1)
            lines.append((0, 0, {'product_id': p.id, 'product_uom_qty': 1}))
        tmpl.sale_order_template_line_ids = lines
    print('Plantilla de presupuesto:', tmpl.name)

env.cr.commit()
print('OK - configuración terminada')
