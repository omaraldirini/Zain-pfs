from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


class PFSPortal(CustomerPortal):
    """Provident Fund self-service portal.

    Employees with portal/internal user access can view their own:
      - Account dashboard (/my/pfs)
      - Personal loans       (/my/pfs/loans, /my/pfs/loans/<id>)
      - Withdrawals          (/my/pfs/withdrawals, /my/pfs/withdrawals/<id>)
      - Land loans           (/my/pfs/land-loans, /my/pfs/land-loans/<id>)
    """

    # ── Home portal entry counts ─────────────────────────────────────────────

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        member = self._get_portal_member()
        if member:
            if 'pfs_active_loan_count' in counters:
                values['pfs_active_loan_count'] = len(
                    member.loan_ids.filtered(lambda l: l.state == 'active')
                )
            if 'pfs_pending_count' in counters:
                pending_states = ('draft', 'submitted', 'preparation',
                                  'approval_1', 'approval_2', 'pending_payment',
                                  'hr_review')
                values['pfs_pending_count'] = len(
                    member.loan_ids.filtered(lambda l: l.state in pending_states)
                ) + len(
                    member.withdrawal_ids.filtered(lambda w: w.state in pending_states)
                )
        else:
            values['pfs_active_loan_count'] = 0
            values['pfs_pending_count'] = 0
        return values

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _get_portal_member(self):
        """Return the zain.member for the current user, or an empty recordset."""
        user = request.env.user
        return request.env['zain.member'].sudo().search(
            [('employee_id.user_id', '=', user.id), ('active', '=', True)],
            limit=1,
        )

    def _state_label(self, model_name, state_value):
        """Return the human-readable label for a Selection field value."""
        fields_info = request.env[model_name].sudo().fields_get(['state'])
        selection = dict(fields_info.get('state', {}).get('selection', []))
        return selection.get(state_value, state_value)

    # ── Dashboard ────────────────────────────────────────────────────────────

    @http.route('/my/pfs', type='http', auth='user', website=True)
    def portal_pfs_dashboard(self, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.render('zain_pfs.portal_no_member', {
                'page_name': 'pfs',
            })

        active_loans = member.loan_ids.filtered(lambda l: l.state == 'active')
        active_land_loans = member.land_loan_ids.filtered(lambda l: l.state == 'active')
        pending_withdrawals = member.withdrawal_ids.filtered(
            lambda w: w.state not in ('approved', 'cancelled')
        )

        return request.render('zain_pfs.portal_pfs_dashboard', {
            'member': member,
            'active_loans': active_loans,
            'active_land_loans': active_land_loans,
            'pending_withdrawals': pending_withdrawals,
            'page_name': 'pfs',
        })

    # ── Personal Loans ───────────────────────────────────────────────────────

    @http.route('/my/pfs/loans', type='http', auth='user', website=True)
    def portal_pfs_loans(self, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        loans = member.loan_ids.filtered(lambda l: l.state != 'cancelled').sorted(
            key=lambda l: l.id, reverse=True
        )
        return request.render('zain_pfs.portal_pfs_loans', {
            'member': member,
            'loans': loans,
            'page_name': 'pfs_loans',
        })

    @http.route('/my/pfs/loans/<int:loan_id>', type='http', auth='user', website=True)
    def portal_pfs_loan_detail(self, loan_id, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        loan = member.loan_ids.filtered(lambda l: l.id == loan_id)
        if not loan:
            return request.not_found()

        return request.render('zain_pfs.portal_pfs_loan_detail', {
            'member': member,
            'loan': loan,
            'page_name': 'pfs_loans',
        })

    # ── Withdrawals ──────────────────────────────────────────────────────────

    @http.route('/my/pfs/withdrawals', type='http', auth='user', website=True)
    def portal_pfs_withdrawals(self, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        withdrawals = member.withdrawal_ids.sorted(key=lambda w: w.id, reverse=True)
        return request.render('zain_pfs.portal_pfs_withdrawals', {
            'member': member,
            'withdrawals': withdrawals,
            'page_name': 'pfs_withdrawals',
        })

    @http.route('/my/pfs/withdrawals/<int:withdrawal_id>', type='http', auth='user', website=True)
    def portal_pfs_withdrawal_detail(self, withdrawal_id, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        withdrawal = member.withdrawal_ids.filtered(lambda w: w.id == withdrawal_id)
        if not withdrawal:
            return request.not_found()

        return request.render('zain_pfs.portal_pfs_withdrawal_detail', {
            'member': member,
            'withdrawal': withdrawal,
            'page_name': 'pfs_withdrawals',
        })

    # ── Land Loans ───────────────────────────────────────────────────────────

    @http.route('/my/pfs/land-loans', type='http', auth='user', website=True)
    def portal_pfs_land_loans(self, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        land_loans = member.land_loan_ids.filtered(lambda l: l.state != 'cancelled').sorted(
            key=lambda l: l.id, reverse=True
        )
        return request.render('zain_pfs.portal_pfs_land_loans', {
            'member': member,
            'land_loans': land_loans,
            'page_name': 'pfs_land_loans',
        })

    @http.route('/my/pfs/land-loans/<int:loan_id>', type='http', auth='user', website=True)
    def portal_pfs_land_loan_detail(self, loan_id, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        loan = member.land_loan_ids.filtered(lambda l: l.id == loan_id)
        if not loan:
            return request.not_found()

        return request.render('zain_pfs.portal_pfs_land_loan_detail', {
            'member': member,
            'loan': loan,
            'page_name': 'pfs_land_loans',
        })
