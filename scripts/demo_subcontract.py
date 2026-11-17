"""Ejemplos de SUBCONTRATISTA y de PROVEEDOR DE MATERIALES sobre los datos DEMO.

Uso:
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf --no-http < scripts/demo_subcontract.py

Requiere scripts/demo_data.py. Idempotente: si ya existe "Climatización Frío Total (DEMO)" no hace nada.

Subcontratista  = vende un SERVICIO con alcance, plazo y avance (se paga por planillas, con retención).
Proveedor       = vende MATERIALES (se paga por lo pedido/entregado, con precios pactados y crédito).
"""
from datetime import date, datetime, timedelta

TODAY = date(2026, 11, 14)
Partner = env['res.partner']
if Partner.search_count([('name', '=', 'Climatización Frío Total (DEMO)')]):
    print("El ejemplo ya existe. Nada que hacer.")
    raise SystemExit(0)


def dt(d, h=9):
    return datetime(d.year, d.month, d.day, h, 0)


def project(fragment):
    p = env['project.project'].search([('name', 'ilike', fragment), ('name', 'ilike', '(DEMO)')], limit=1)
    assert p, f"Falta el proyecto DEMO '{fragment}': corra primero scripts/demo_data.py"
    return p


def tag(model, name):
    return env[model].search([('name', '=', name)], limit=1) or env[model].create({'name': name})


def term(days):
    return env['account.payment.term'].search([('name', '=', f'{days} días')], limit=1)


def stage(name):
    return env['project.task.type'].search([('name', '=', name)], limit=1)


def pay(move, d):
    env['account.payment.register'].with_context(active_model='account.move', active_ids=move.ids).create(
        {'payment_date': d}).action_create_payments()


company = env.company
p2 = project('Inversiones Pacífico')
p3 = project('Distribuidora Sierra')
carlos = env['res.users'].search([('login', '=', 'carlos.mendoza@demo.local')], limit=1) or env.ref('base.user_admin')
subcontract = env['product.product'].search([('name', '=', 'Subcontrato')], limit=1)
dist = lambda pr: {str(pr.account_id.id): 100}  # noqa: E731

tag_sub = tag('res.partner.category', 'Subcontratista')
tag_prov = tag('res.partner.category', 'Proveedor de materiales')

# Cuenta para retener la garantía de los subcontratistas (se devuelve al entregar la obra)
Account = env['account.account']
ret_account = Account.search([('code', '=', '230600')], limit=1) or Account.create({
    'name': 'Retenciones de garantía a subcontratistas', 'code': '230600',
    'account_type': 'liability_current', 'company_ids': [(6, 0, company.ids)],
})

# =============================================================================
# 1. SUBCONTRATISTA: climatización del local comercial
# =============================================================================
print("\n== Subcontratista")
sub = Partner.create({
    'name': 'Climatización Frío Total (DEMO)', 'is_company': True, 'supplier_rank': 1, 'city': 'Guayaquil',
    'category_id': [(6, 0, tag_sub.ids)], 'property_supplier_payment_term_id': term(15).id,
    'comment': '<p>Subcontratista de climatización (VRF y ductos). Retención de garantía 5% hasta la entrega.</p>',
})
residente = Partner.create({
    'name': 'Ing. Pablo Vega (DEMO)', 'parent_id': sub.id, 'type': 'contact',
    'function': 'Residente de obra', 'email': 'pablo.vega@example.com', 'phone': '+593 99 000 0000',
})
rival = Partner.create({
    'name': 'Aire Andino Climatización (DEMO)', 'is_company': True, 'supplier_rank': 1,
    'category_id': [(6, 0, tag_sub.ids)],
})

partidas = [
    ('Partida 1: Suministro de equipos VRF (3 unidades condensadoras)', 9000.0),
    ('Partida 2: Instalación de ductos, tuberías de cobre y rejillas', 6000.0),
    ('Partida 3: Pruebas, balanceo y puesta en marcha', 3000.0),
]


def subcontract_rfq(vendor, prices, d, ref):
    return env['purchase.order'].create({
        'partner_id': vendor.id, 'date_order': dt(d), 'partner_ref': ref, 'origin': p2.name,
        'payment_term_id': vendor.property_supplier_payment_term_id.id,
        'note': '<p>Retención de garantía del 5% en cada planilla, devuelta al recibir la obra a satisfacción.</p>',
        'order_line': [(0, 0, {'product_id': subcontract.id, 'name': name, 'product_qty': 1, 'price_unit': price,
                               'analytic_distribution': dist(p2), 'date_planned': dt(date(2026, 12, 18))})
                       for (name, _p), price in zip(partidas, prices)],
    })


# Comparativo de cotizaciones: gana Frío Total, se cancela Aire Andino
loser = subcontract_rfq(rival, [9900, 6600, 3300], date(2026, 10, 9), 'AA-COT-0457')
po_sub = subcontract_rfq(sub, [p for _n, p in partidas], date(2026, 10, 12), 'FT-COT-2026-118')
loser.button_cancel()
loser.message_post(body="Cotización no adjudicada: 10% más cara y plazo de 8 semanas (Frío Total ofrece 6).")
po_sub.button_confirm()
po_sub.date_approve = dt(date(2026, 10, 14))
po_sub.message_post(body="Subcontrato adjudicado. Contrato firmado el 14/10/2026. Inicio en obra: 02/11/2026.")
print(f"   Comparativo: {loser.name} cancelada (${loser.amount_untaxed:,.0f}) | {po_sub.name} adjudicada (${po_sub.amount_untaxed:,.0f})")

# Tarea del subcontrato en el proyecto (aparece en kanban y línea de tiempo)
tag_sc = tag('project.tags', 'subcontrato')
wo2 = p2.task_ids.filtered(lambda t: t.name == 'WO-02 Estructura metálica')
sc_task = env['project.task'].create({
    'name': 'SC-01 Climatización - Frío Total', 'project_id': p2.id, 'tag_ids': [(6, 0, tag_sc.ids)],
    'user_ids': [(6, 0, carlos.ids)], 'partner_id': sub.id, 'stage_id': stage('En progreso').id,
    'planned_date_start': dt(date(2026, 11, 2), 8), 'planned_date_end': dt(date(2026, 12, 18), 17),
    'date_deadline': dt(date(2026, 12, 18), 17), 'depend_on_ids': [(6, 0, wo2.ids)],
    'description': (
        f"<p><b>Subcontratista:</b> {sub.name} — residente {residente.name} ({residente.email})</p>"
        f"<p><b>Contrato:</b> orden de compra {po_sub.name} por ${po_sub.amount_untaxed:,.2f} + IVA</p>"
        "<p><b>Alcance:</b> suministro e instalación de climatización VRF para 650 m2.</p>"
        "<p><b>Pago:</b> por planillas de avance aprobadas por el residente de BizFlow. Retención 5%.</p>"),
})
sc_task.message_subscribe(partner_ids=residente.ids)
for name, done, a, b in [('Planilla 1 - Equipos entregados en obra', True, 0, 7),
                         ('Planilla 2 - Ductos 50%', True, 7, 18),
                         ('Planilla 3 - Ductos 100%', False, 18, 32),
                         ('Planilla 4 - Pruebas y puesta en marcha', False, 32, 46)]:
    vals = {'name': name, 'project_id': p2.id, 'parent_id': sc_task.id, 'tag_ids': [(6, 0, tag_sc.ids)],
            'stage_id': stage('Hecho' if done else 'Por hacer').id, 'user_ids': [(6, 0, carlos.ids)],
            'planned_date_start': dt(date(2026, 11, 2) + timedelta(days=a), 8),
            'planned_date_end': dt(date(2026, 11, 2) + timedelta(days=b), 17)}
    if done:
        vals['state'] = '1_done'
    env['project.task'].create(vals)


def planilla(n, qty_by_partida, d, ref, pay_date=None):
    """Factura del subcontratista por avance: cantidad = fracción ejecutada de cada partida."""
    before = po_sub.invoice_ids
    po_sub.action_create_invoice()
    bill = po_sub.invoice_ids - before
    for line in bill.invoice_line_ids.filtered('purchase_line_id'):
        qty = qty_by_partida.get(line.purchase_line_id.name[:9], 0)
        if qty:
            line.quantity = qty
        else:
            line.unlink()
    base = sum(bill.invoice_line_ids.mapped('price_subtotal'))
    bill.write({
        'invoice_date': d, 'ref': ref,
        'invoice_line_ids': [(0, 0, {
            'name': f'Retención de garantía 5% - Planilla {n}', 'account_id': ret_account.id,
            'quantity': 1, 'price_unit': -round(base * 0.05, 2), 'tax_ids': [(5, 0, 0)],
        })],
    })
    bill.action_post()
    sc_task.message_post(body=f"Planilla {n} aprobada: avance por ${base:,.2f}. Factura {ref} registrada.")
    if pay_date:
        pay(bill, pay_date)
    return bill, base


b1, base1 = planilla(1, {'Partida 1': 1.0}, date(2026, 11, 6), 'FT-001-001-000231', pay_date=date(2026, 11, 13))
b2, base2 = planilla(2, {'Partida 2': 0.5}, date(2026, 11, 13), 'FT-001-001-000247')
print(f"   Planilla 1 {b1.name}: avance ${base1:,.0f}, retención ${base1*0.05:,.0f}, a pagar ${b1.amount_total:,.2f} → {b1.payment_state}")
print(f"   Planilla 2 {b2.name}: avance ${base2:,.0f}, retención ${base2*0.05:,.0f}, a pagar ${b2.amount_total:,.2f} → {b2.payment_state}"
      f" (vence {b2.invoice_date_due})")
billed = sum(l.qty_invoiced * l.price_unit for l in po_sub.order_line)
print(f"   Contrato ${po_sub.amount_untaxed:,.0f} | ejecutado y facturado ${billed:,.0f} ({billed/po_sub.amount_untaxed:.0%})"
      f" | saldo por ejecutar ${po_sub.amount_untaxed - billed:,.0f} | garantía retenida ${(base1+base2)*0.05:,.0f}")

# =============================================================================
# 2. PROVEEDOR DE MATERIALES
# =============================================================================
print("\n== Proveedor de materiales")
cementos = Partner.search([('name', '=', 'Cementos y Agregados del Sur (DEMO)')], limit=1)
cementos.write({'category_id': [(4, tag_prov.id)], 'property_supplier_payment_term_id': term(30).id})
for name in ['Ferretería El Constructor (DEMO)', 'Pinturas y Acabados Color (DEMO)']:
    Partner.search([('name', '=', name)]).category_id = [(4, tag_prov.id)]
Partner.search([('name', '=', 'Instalaciones Eléctricas Voltio (DEMO)')]).category_id = [(4, tag_sub.id)]

aceros = Partner.create({
    'name': 'Aceros Andinos (DEMO)', 'is_company': True, 'supplier_rank': 1, 'city': 'Quito',
    'category_id': [(6, 0, tag_prov.ids)], 'property_supplier_payment_term_id': term(30).id,
})
unit = env.ref('uom.product_uom_unit')
steel = {}
for name, cost in [('Perfil estructural IPE 200 x 6 m (DEMO)', 145.0),
                   ('Plancha galvanizada cubierta 0.4 mm (DEMO)', 28.0),
                   ('Perno de anclaje 3/4" (DEMO)', 3.2)]:
    steel[name] = env['product.product'].create({
        'name': name, 'type': 'consu', 'purchase_ok': True, 'sale_ok': False, 'standard_price': cost,
        'uom_id': unit.id, 'purchase_method': 'purchase',
        'seller_ids': [(0, 0, {'partner_id': aceros.id, 'price': cost, 'delay': 5})],
    })

# 2a. Acuerdo marco de precios con la cementera (precios fijos hasta junio 2027)
cement = env['product.product'].search([('name', '=', 'Cemento portland saco 50kg (DEMO)')], limit=1)
block = env['product.product'].search([('name', '=', 'Bloque de hormigón 15cm (DEMO)')], limit=1)
blanket = env['purchase.requisition'].create({
    'vendor_id': cementos.id, 'requisition_type': 'blanket_order', 'reference': 'CONV-CEM-2026',
    'date_start': date(2026, 11, 1), 'date_end': date(2027, 6, 30), 'user_id': carlos.id,
    'description': '<p>Precios fijos para todas las obras. Entrega en obra incluida en pedidos sobre 200 sacos.</p>',
    'line_ids': [(0, 0, {'product_id': cement.id, 'product_qty': 3000, 'price_unit': 7.40}),
                 (0, 0, {'product_id': block.id, 'product_qty': 40000, 'price_unit': 0.44})],
})
blanket.action_confirm()
po_cem = env['purchase.order'].create({
    'partner_id': cementos.id, 'requisition_id': blanket.id, 'date_order': dt(date(2026, 11, 13)),
    'payment_term_id': term(30).id,
    'origin': p3.name, 'partner_ref': 'Pedido obra Sierra #1',
    'order_line': [(0, 0, {'product_id': cement.id, 'product_qty': 320, 'price_unit': 7.40,
                           'analytic_distribution': dist(p3), 'date_planned': dt(date(2026, 11, 21))}),
                   (0, 0, {'product_id': block.id, 'product_qty': 5000, 'price_unit': 0.44,
                           'analytic_distribution': dist(p3), 'date_planned': dt(date(2026, 11, 21))})],
})
po_cem.button_confirm()
po_cem.date_approve = dt(date(2026, 11, 13))
print(f"   Acuerdo marco {blanket.name} ({cementos.name}): cemento $7,40 (lista $7,80), bloque $0,44 (lista $0,48)")
print(f"   Pedido contra el acuerdo {po_cem.name}: ${po_cem.amount_untaxed:,.2f} → obra Sierra, entrega 21/11 (por facturar)")

# 2b. Compra de acero con crédito a 30 días, facturada y pendiente de pago
po_steel = env['purchase.order'].create({
    'partner_id': aceros.id, 'date_order': dt(date(2026, 11, 10)), 'origin': p3.name,
    'payment_term_id': term(30).id,
    'partner_ref': 'AA-PROF-8812',
    'order_line': [(0, 0, {'product_id': p.id, 'product_qty': q, 'price_unit': p.standard_price,
                           'analytic_distribution': dist(p3), 'date_planned': dt(date(2026, 11, 17))})
                   for p, q in [(steel['Perfil estructural IPE 200 x 6 m (DEMO)'], 40),
                                (steel['Plancha galvanizada cubierta 0.4 mm (DEMO)'], 220),
                                (steel['Perno de anclaje 3/4" (DEMO)'], 160)]],
})
po_steel.button_confirm()
po_steel.date_approve = dt(date(2026, 11, 10))
po_steel.action_create_invoice()
bill_steel = po_steel.invoice_ids
bill_steel.write({'invoice_date': date(2026, 11, 12), 'ref': 'AA-001-002-004571'})
bill_steel.action_post()
print(f"   {po_steel.name} {aceros.name}: ${po_steel.amount_untaxed:,.2f} | factura {bill_steel.name} "
      f"${bill_steel.amount_total:,.2f} vence {bill_steel.invoice_date_due} → {bill_steel.payment_state}")

env.cr.commit()
print("\nOK - ejemplos de subcontratista y proveedor creados")
