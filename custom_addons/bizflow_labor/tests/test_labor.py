from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLabor(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({
            "labor_daily_hours": 8.0,
            "labor_overtime_factor": 1.5,
            "labor_extra_factor": 2.0,
            "labor_weekend_is_extra": True,
            "labor_cost_factor": 1.4,
            "labor_max_overtime_week": 12.0,
        })
        cls.company._bizflow_labor_setup_accounting()
        cls.project = cls.env["project.project"].create({"name": "Obra test", "allow_timesheets": True})
        cls.project2 = cls.env["project.project"].create({"name": "Obra test 2", "allow_timesheets": True})
        cls.task = cls.env["project.task"].create({"name": "Ejecución", "project_id": cls.project.id})
        cls.task2 = cls.env["project.task"].create({"name": "Otra", "project_id": cls.project2.id})
        cls.emp = cls.env["hr.employee"].create({"name": "Maestro test", "labor_pay_rate": 4.0})
        # Semana sin datos del demo: lunes 4 al domingo 10 de marzo de 2030
        cls.monday = date(2030, 3, 4)

    def _ts(self, day, hours, project=None, task=None, employee=None):
        project = project or self.project
        return self.env["account.analytic.line"].create({
            "name": "trabajo",
            "project_id": project.id,
            "task_id": (task or (self.task if project == self.project else self.task2)).id,
            "employee_id": (employee or self.emp).id,
            "unit_amount": hours,
            "date": day,
        })

    def _period(self):
        return self.env["bizflow.labor.period"].create({"date_from": self.monday, "date_to": date(2030, 3, 10)})

    def test_hourly_cost_from_rate(self):
        self.assertAlmostEqual(self.emp.hourly_cost, 5.6)
        self.emp.labor_cost_factor = 1.5
        self.assertAlmostEqual(self.emp.hourly_cost, 6.0)

    def test_classification_and_amounts(self):
        for i in range(5):  # lun-vie 9 h → 8 normales + 1 suplementaria por día
            self._ts(date(2030, 3, 4 + i), 9)
        self._ts(date(2030, 3, 9), 4)  # sábado → extraordinarias
        period = self._period()
        period.action_generate()
        line = period.line_ids
        self.assertEqual(len(line), 1)
        self.assertAlmostEqual(line.hours_normal, 40)
        self.assertAlmostEqual(line.hours_overtime, 5)
        self.assertAlmostEqual(line.hours_extra, 4)
        # 40×4 + 5×4×1.5 + 4×4×2 = 160 + 30 + 32
        self.assertAlmostEqual(line.amount_total, 222.0)
        # recargo al proyecto: costo/h 5.6 → 5×5.6×0.5 + 4×5.6×1.0 = 14 + 22.4
        self.assertAlmostEqual(line.cost_premium, 36.4)
        self.assertFalse(line.warning)

    def test_overtime_split_across_projects(self):
        self._ts(self.monday, 6, project=self.project)
        self._ts(self.monday, 4, project=self.project2)  # las 2 últimas horas del día son suplementarias
        period = self._period()
        period.action_generate()
        pl = {p.project_id: p for p in period.project_line_ids}
        self.assertAlmostEqual(pl[self.project].hours_normal, 6)
        self.assertAlmostEqual(pl[self.project2].hours_normal, 2)
        self.assertAlmostEqual(pl[self.project2].hours_overtime, 2)

    def test_holiday_is_extra(self):
        self.env["resource.calendar.leaves"].create({
            "name": "Feriado",
            "date_from": "2030-03-05 00:00:00",
            "date_to": "2030-03-05 23:59:59",
            "company_id": self.company.id,
        })
        self._ts(date(2030, 3, 5), 8)
        period = self._period()
        period.action_generate()
        self.assertAlmostEqual(period.line_ids.hours_extra, 8)

    def test_overtime_cap_warning(self):
        for i in range(5):
            self._ts(date(2030, 3, 4 + i), 11)  # 15 h suplementarias > tope 12
        period = self._period()
        period.action_generate()
        self.assertIn("suplementarias", period.line_ids.warning)
        self.assertEqual(period.warning_count, 1)

    def test_full_flow_lock_post_pay(self):
        ts = self._ts(self.monday, 10)
        period = self._period()
        period.action_generate()
        period.action_approve()
        self.assertTrue(ts.labor_locked)
        with self.assertRaises(UserError):
            ts.unit_amount = 5
        with self.assertRaises(UserError):
            ts.unlink()
        ts.name = "Descripción corregida"  # campos no protegidos sí se pueden editar

        period.action_post()
        line = period.line_ids
        move = line.move_id
        self.assertEqual(move.state, "posted")
        # 8×4 + 2×4×1.5 = 44
        self.assertAlmostEqual(move.amount_total, 44.0)
        self.assertFalse(any(move.line_ids.mapped("analytic_distribution")), "El pago no debe llevar analítica")
        premium = period.premium_line_ids
        self.assertEqual(len(premium), 1)
        self.assertEqual(premium.account_id, self.project.account_id)
        self.assertAlmostEqual(premium.amount, -(2 * 5.6 * 0.5))

        journal = self.company.labor_payment_journal_id
        self.env["bizflow.labor.pay.wizard"].create({"period_id": period.id, "journal_id": journal.id}).action_pay()
        self.assertEqual(period.state, "paid")
        self.assertTrue(line.payment_id)
        payable = move.line_ids.filtered(lambda l: l.account_id == self.company.labor_payable_account_id)
        self.assertTrue(payable.reconciled)

    def test_unpost_and_reset(self):
        self._ts(self.monday, 10)
        period = self._period()
        period.action_generate()
        period.action_approve()
        period.action_post()
        move = period.line_ids.move_id
        period.action_unpost()
        self.assertEqual(move.state, "cancel")
        self.assertFalse(period.premium_line_ids)
        self.assertEqual(period.state, "approved")
        period.action_reset_draft()
        self.assertEqual(period.state, "draft")

    def test_approve_requires_rate(self):
        other = self.env["hr.employee"].create({"name": "Sin tarifa"})
        self._ts(self.monday, 8, employee=other)
        period = self._period()
        period.action_generate()
        with self.assertRaises(UserError):
            period.action_approve()

    def test_no_overlap_and_no_double_count(self):
        self._ts(self.monday, 8)
        period = self._period()
        period.action_generate()
        with self.assertRaises(ValidationError):
            self._period()
        period.action_cancel()
        self.assertFalse(period.timesheet_ids)
        period2 = self._period()
        period2.action_generate()
        self.assertAlmostEqual(period2.hours_total, 8)

    def test_team_restriction(self):
        outsider = self.env["hr.employee"].create({"name": "Externo", "labor_pay_rate": 3})
        self.project.write({"labor_employee_ids": [(6, 0, self.emp.ids)], "labor_restrict_team": True})
        self._ts(self.monday, 8)
        with self.assertRaises(ValidationError):
            self._ts(self.monday, 8, employee=outsider)

    def test_weekly_entry(self):
        self.project.labor_employee_ids = [(6, 0, self.emp.ids)]
        wizard = self.env["bizflow.labor.weekly.entry"].with_context(default_project_id=self.project.id).create({
            "project_id": self.project.id,
            "task_id": self.task.id,
            "week_start": date(2030, 3, 6),  # miércoles → se alinea al lunes
        })
        self.assertEqual(wizard.line_ids.employee_id, self.emp)
        wizard.line_ids.write({"h0": 8, "h1": 8, "h5": 4})
        wizard.action_confirm()
        ts = self.env["account.analytic.line"].search([("employee_id", "=", self.emp.id), ("project_id", "=", self.project.id)])
        self.assertEqual(sorted(ts.mapped("date")), [date(2030, 3, 4), date(2030, 3, 5), date(2030, 3, 9)])
        self.assertAlmostEqual(sum(ts.mapped("unit_amount")), 20)

    def test_cron_creates_previous_period(self):
        Period = self.env["bizflow.labor.period"]
        Period.search([]).filtered(lambda p: p.state in ("draft", "cancel")).unlink()
        Period._cron_create_previous_period()
        self.assertTrue(Period.search_count([("company_id", "=", self.company.id)]))

    def test_hours_alert(self):
        self.task.allocated_hours = 4
        self.project.user_id = self.env.user
        self._ts(self.monday, 6)
        self.env["project.task"]._cron_labor_hours_alert()
        self.assertTrue(self.task.activity_ids)
        self.env["project.task"]._cron_labor_hours_alert()
        self.assertEqual(len(self.task.activity_ids), 1, "No debe duplicar la alerta")
