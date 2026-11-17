from odoo import _, api, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    @api.model
    def _cron_labor_hours_alert(self):
        """Crea una actividad al gerente del proyecto cuando una tarea supera sus horas planificadas."""
        activity_type = self.env.ref("mail.mail_activity_data_warning", raise_if_not_found=False) or self.env.ref(
            "mail.mail_activity_data_todo"
        )
        summary = _("Horas planificadas superadas")
        tasks = self.search([
            ("allocated_hours", ">", 0),
            ("is_template", "=", False),
            ("project_id.is_template", "=", False),
            ("state", "not in", ("1_done", "1_canceled")),
        ]).filtered(lambda t: t.effective_hours > t.allocated_hours)
        for task in tasks:
            responsible = task.project_id.user_id or task.user_ids[:1]
            if not responsible:
                continue
            if task.activity_ids.filtered(lambda a: a.summary == summary):
                continue
            task.activity_schedule(
                activity_type_id=activity_type.id,
                summary=summary,
                note=_(
                    "La tarea lleva %(eff).1f h registradas y tenía %(alloc).1f h planificadas.",
                    eff=task.effective_hours, alloc=task.allocated_hours,
                ),
                user_id=responsible.id,
            )
