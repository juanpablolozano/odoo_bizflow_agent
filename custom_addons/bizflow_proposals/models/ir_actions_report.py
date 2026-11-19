# -*- coding: utf-8 -*-
import io
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    def _render_qweb_pdf_prepare_streams(self, report_ref, data, res_ids=None):
        collected_streams = super()._render_qweb_pdf_prepare_streams(
            report_ref, data, res_ids=res_ids
        )
        if not collected_streams or not res_ids:
            return collected_streams
        if not self._is_sale_order_report(report_ref):
            return collected_streams

        SaleOrder = self.env["sale.order"]
        for order_id in list(collected_streams.keys()):
            order = SaleOrder.browse(order_id).exists()
            if not order:
                continue
            proposal = order.bizflow_proposal_id
            if not proposal:
                continue
            try:
                pdf_bytes = proposal.export_pdf_bytes()
            except Exception:
                _logger.exception(
                    "BizFlow PDF for quotation %s failed; keeping standard report",
                    order.name,
                )
                continue
            old = collected_streams[order_id].get("stream")
            if old:
                try:
                    old.close()
                except Exception:
                    pass
            collected_streams[order_id]["stream"] = io.BytesIO(pdf_bytes)
        return collected_streams
