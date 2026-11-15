# -*- coding: utf-8 -*-
from odoo import fields, http
from odoo.http import request


class BizflowProposalPortal(http.Controller):

    def _get_link(self, token):
        return request.env["bizflow.share.link"]._get_by_token(token)

    def _access_gate(self, proposal, code=None):
        if not proposal.access_required:
            return None
        result = proposal.check_access_code(code or "")
        if result.get("ok"):
            return None
        locked = result.get("locked")
        err = result.get("message") or "Invalid access code."
        color = "#b00020"
        return f"""<!DOCTYPE html><html><head><meta name="robots" content="noindex"/>
        <meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline'"/>
        </head><body style="font-family:Georgia,serif;padding:48px;background:#f7f5f0">
        <div style="max-width:420px;margin:auto;background:#fff;padding:32px;border-radius:8px">
        <h2>Protected proposal</h2>
        <p style="color:{color}">{err}</p>
        {"<p>Locked for 15 minutes.</p>" if locked else ""}
        </div></body></html>"""

    @http.route(
        "/bizflow/p/<string:token>",
        type="http",
        auth="public",
        website=False,
        csrf=False,
    )
    def public_proposal(self, token, access_code=None, **kwargs):
        link = self._get_link(token)
        if not link:
            return request.make_response("Proposal link not found", status=404)
        proposal = link.proposal_id.sudo()
        code = access_code or kwargs.get("code") or request.httprequest.cookies.get(
            f"bizflow_access_{proposal.id}"
        )
        if proposal.access_required and not code:
            html = f"""<!DOCTYPE html><html><head><meta name="robots" content="noindex"/></head>
            <body style="font-family:Georgia,serif;padding:48px;background:#f7f5f0">
            <form method="POST" action="/bizflow/p/{token}/access"
                  style="max-width:420px;margin:auto;background:#fff;padding:32px;border-radius:8px">
            <h2>Protected proposal</h2>
            <label>Access code</label><br/>
            <input name="access_code" style="width:100%;padding:10px;margin:8px 0 16px"/>
            <button type="submit" style="padding:10px 16px;background:#1B4D3E;color:#fff;border:0;border-radius:4px">Open</button>
            </form></body></html>"""
            return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])
        if proposal.access_required:
            gate = self._access_gate(proposal, code)
            if gate:
                return request.make_response(gate, headers=[("Content-Type", "text/html; charset=utf-8")])
        link.sudo().track_view(duration_seconds=0)
        review_bar = ""
        if link.purpose == "review":
            review_bar = f"""
            <div style="position:sticky;top:0;background:#1c2430;color:#fff;padding:12px 20px;z-index:9;
                        display:flex;gap:12px;align-items:center;font-family:system-ui,sans-serif">
              <strong>Review</strong>
              <form method="POST" action="/bizflow/p/{token}/decision" style="display:flex;gap:8px;flex:1;margin:0">
                <input type="hidden" name="decision" id="bf_decision" value="approve"/>
                <input name="author_name" placeholder="Your name" style="padding:6px 10px;border-radius:6px;border:0"/>
                <input name="note" placeholder="Note (required for changes)" style="flex:1;padding:6px 10px;border-radius:6px;border:0"/>
                <button type="submit" onclick="document.getElementById('bf_decision').value='approve'"
                        style="padding:6px 12px;border:0;border-radius:6px;background:#2f6f8f;color:#fff">Approve</button>
                <button type="submit" onclick="document.getElementById('bf_decision').value='changes'"
                        style="padding:6px 12px;border:0;border-radius:6px;background:#a65d3b;color:#fff">Request changes</button>
              </form>
            </div>
            <form method="POST" action="/bizflow/p/{token}/comments" style="max-width:820px;margin:16px auto;padding:0 16px;font-family:system-ui,sans-serif">
              <input name="author_name" placeholder="Name" style="padding:8px;margin-right:8px"/>
              <input name="section" placeholder="Section" style="padding:8px;margin-right:8px;width:120px"/>
              <input name="body" placeholder="Comment" style="padding:8px;width:50%"/>
              <button type="submit" style="padding:8px 12px;margin-left:8px">Comment</button>
            </form>
            """
        track_script = f"""
        <script>
        setTimeout(function(){{
          navigator.sendBeacon('/bizflow/p/{token}/track', new URLSearchParams({{duration: '3'}}));
        }}, 3000);
        </script>
        """
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
            "<meta name='robots' content='noindex'/>"
            "</head><body style='margin:0'>"
            + review_bar
            + str(proposal.render_html())
            + track_script
            + "</body></html>"
        )
        response = request.make_response(
            html,
            headers=[
                ("Content-Type", "text/html; charset=utf-8"),
                ("X-Robots-Tag", "noindex"),
            ],
        )
        if proposal.access_required and code:
            response.set_cookie(
                f"bizflow_access_{proposal.id}",
                code.upper(),
                max_age=12 * 3600,
            )
        return response

    @http.route(
        "/bizflow/p/<string:token>/access",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def public_access(self, token, **post):
        return request.redirect(f"/bizflow/p/{token}?code={post.get('access_code', '')}")

    @http.route(
        "/bizflow/p/<string:token>/track",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def public_track(self, token, **post):
        link = self._get_link(token)
        if not link:
            return request.make_response("ok", status=204)
        duration = post.get("duration") or request.httprequest.form.get("duration") or 0
        link.sudo().track_view(duration_seconds=duration)
        return request.make_response("ok", status=204)

    @http.route(
        "/bizflow/p/<string:token>/comments",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def public_comment(self, token, **post):
        link = self._get_link(token)
        if not link:
            return request.not_found()
        body = (post.get("body") or "").strip()
        if body:
            request.env["bizflow.proposal.comment"].sudo().create(
                {
                    "proposal_id": link.proposal_id.id,
                    "share_link_id": link.id,
                    "section": post.get("section") or "",
                    "author_name": post.get("author_name") or "Client",
                    "author_email": post.get("author_email") or "",
                    "body": body,
                }
            )
        return request.redirect(f"/bizflow/p/{token}")

    @http.route(
        "/bizflow/p/<string:token>/decision",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def public_decision(self, token, **post):
        link = self._get_link(token)
        if not link:
            return request.not_found()
        decision = post.get("decision") or "approve"
        note = (post.get("note") or "").strip()
        if decision == "changes" and not note:
            return request.make_response(
                "A note is required when requesting changes.",
                status=400,
            )
        request.env["bizflow.proposal.decision"].sudo().create(
            {
                "proposal_id": link.proposal_id.id,
                "share_link_id": link.id,
                "decision": decision,
                "note": note,
                "author_name": post.get("author_name") or "Client",
                "author_email": post.get("author_email") or "",
            }
        )
        if decision == "approve":
            link.proposal_id.sudo().write({"state": "accepted"})
        elif decision == "changes":
            link.proposal_id.sudo().write({"state": "review"})
        return request.redirect(f"/bizflow/p/{token}")

    @http.route(
        "/bizflow/sign/<string:token>",
        type="http",
        auth="public",
        csrf=False,
    )
    def public_sign(self, token, access_code=None, **kwargs):
        req = request.env["bizflow.signing.request"]._get_by_token(token)
        if not req:
            return request.make_response("Signing link not found", status=404)
        code = access_code or kwargs.get("code")
        if not code or code.upper() != (req.access_code or "").upper():
            html = f"""<!DOCTYPE html><html><body style="font-family:Georgia,serif;padding:48px;background:#f7f5f0">
            <form method="GET" action="/bizflow/sign/{token}" style="max-width:420px;margin:auto;background:#fff;padding:32px;border-radius:8px">
            <h2>Sign proposal</h2>
            <label>Access code</label><br/>
            <input name="code" style="width:100%;padding:10px;margin:8px 0 16px"/>
            <button type="submit" style="padding:10px 16px;background:#1B4D3E;color:#fff;border:0;border-radius:4px">Open</button>
            </form></body></html>"""
            return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])
        doc = req.frozen_html or str(req.proposal_id.render_html())
        form = ""
        if not req.client_signed and req.status == "open":
            form = f"""
            <form method="POST" action="/bizflow/sign/{token}/sign" style="max-width:820px;margin:24px auto;padding:20px;
                  background:#fff;border-radius:8px;font-family:system-ui,sans-serif;border:1px solid #ddd">
              <input type="hidden" name="code" value="{code}"/>
              <h3>Client signature</h3>
              <label>Full name</label>
              <input name="signer_name" required style="width:100%;padding:8px;margin:6px 0 12px"/>
              <label>Kickoff date (min 7 days from today)</label>
              <input type="date" name="kickoff_date" required style="width:100%;padding:8px;margin:6px 0 12px"/>
              <button type="submit" style="padding:10px 16px;background:#1B4D3E;color:#fff;border:0;border-radius:4px">Sign</button>
            </form>
            """
        elif req.client_signed:
            form = f"<p style='text-align:center;font-family:system-ui'>Signed by {req.client_signer} · kickoff {req.kickoff_date or 'TBD'}</p>"
        html = f"<!DOCTYPE html><html><head><meta name='robots' content='noindex'/></head><body>{doc}{form}</body></html>"
        return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])

    @http.route(
        "/bizflow/sign/<string:token>/sign",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def public_sign_submit(self, token, **post):
        req = request.env["bizflow.signing.request"]._get_by_token(token)
        if not req:
            return request.not_found()
        code = post.get("code") or ""
        if code.upper() != (req.access_code or "").upper():
            return request.redirect(f"/bizflow/sign/{token}")
        try:
            req.sudo().action_client_sign(
                signer_name=post.get("signer_name") or "",
                kickoff_date=post.get("kickoff_date") or None,
            )
        except Exception as exc:
            return request.make_response(str(exc), status=400)
        return request.redirect(f"/bizflow/sign/{token}?code={code}")

    @http.route(
        "/bizflow/sign/<string:token>/document",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def public_sign_document(self, token, **kwargs):
        req = request.env["bizflow.signing.request"]._get_by_token(token)
        if not req:
            return request.not_found()
        html = req.frozen_html or str(req.proposal_id.render_html())
        return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])
