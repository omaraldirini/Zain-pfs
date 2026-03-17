from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.exceptions import UserError, ValidationError


class PFSPortal(CustomerPortal):
    """Provident Fund self-service portal.

    Employees with portal/internal user access can view their own:
      - Account dashboard    (/my/pfs)
      - Personal loans       (/my/pfs/loans, /my/pfs/loans/new, /my/pfs/loans/<id>)
      - Withdrawals          (/my/pfs/withdrawals, /my/pfs/withdrawals/new, /my/pfs/withdrawals/<id>)
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

    def _loan_apply_render(self, member, errors=None, form_data=None):
        config = request.env['zain.configuration'].sudo()._get_config()
        min_months = config.min_contribution_months
        active_loans = member.loan_ids.filtered(lambda l: l.state == 'active')
        return request.render('zain_pfs.portal_loan_apply', {
            'member': member,
            'min_months': min_months,
            'can_apply': (member.state == 'active'
                          and member.contribution_months >= min_months),
            'active_loans': active_loans,
            'errors': errors or {},
            'form_data': form_data or {},
            'page_name': 'pfs_loans',
        })

    def _withdrawal_apply_render(self, member, errors=None, form_data=None):
        config = request.env['zain.configuration'].sudo()._get_config()
        min_50 = config.withdrawal_50_min_months
        min_75 = config.withdrawal_75_min_months
        months = member.contribution_months
        return request.render('zain_pfs.portal_withdrawal_apply', {
            'member': member,
            'min_50': min_50,
            'min_75': min_75,
            'can_50': member.state == 'active' and months >= min_50,
            'can_75': member.state == 'active' and months >= min_75,
            'errors': errors or {},
            'form_data': form_data or {},
            'page_name': 'pfs_withdrawals',
        })

    # ── Dashboard ────────────────────────────────────────────────────────────

    @http.route('/my/pfs', type='http', auth='user', website=True)
    def portal_pfs_dashboard(self, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.render('zain_pfs.portal_no_member', {'page_name': 'pfs'})

        active_loans = member.loan_ids.filtered(lambda l: l.state == 'active')
        active_land_loans = member.land_loan_ids.filtered(lambda l: l.state == 'active')
        pending_withdrawals = member.withdrawal_ids.filtered(
            lambda w: w.state not in ('approved', 'cancelled')
        )
        latest_resignation = member.resignation_ids.sorted(
            key=lambda r: r.id, reverse=True
        )[:1]
        return request.render('zain_pfs.portal_pfs_dashboard', {
            'member': member,
            'active_loans': active_loans,
            'active_land_loans': active_land_loans,
            'pending_withdrawals': pending_withdrawals,
            'latest_resignation': latest_resignation,
            'page_name': 'pfs',
        })

    # ── Personal Loans ───────────────────────────────────────────────────────

    @http.route('/my/pfs/loans', type='http', auth='user', website=True)
    def portal_pfs_loans(self, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        config = request.env['zain.configuration'].sudo()._get_config()
        loans = member.loan_ids.filtered(lambda l: l.state != 'cancelled').sorted(
            key=lambda l: l.id, reverse=True
        )
        return request.render('zain_pfs.portal_pfs_loans', {
            'member': member,
            'loans': loans,
            'can_apply': (member.state == 'active'
                          and member.contribution_months >= config.min_contribution_months),
            'page_name': 'pfs_loans',
        })

    @http.route('/my/pfs/loans/new', type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def portal_pfs_loan_new(self, **post):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        if request.httprequest.method == 'GET':
            return self._loan_apply_render(member)

        errors = {}
        try:
            loan_amount = float(post.get('loan_amount') or 0)
        except (ValueError, TypeError):
            loan_amount = 0.0
            errors['loan_amount'] = 'Enter a valid number.'

        try:
            installments = int(post.get('installments') or 0)
        except (ValueError, TypeError):
            installments = 0
            errors['installments'] = 'Enter a whole number.'

        try:
            other_income = float(post.get('other_income') or 0)
        except (ValueError, TypeError):
            other_income = 0.0

        try:
            bank_installment = float(post.get('bank_installment') or 0)
        except (ValueError, TypeError):
            bank_installment = 0.0

        is_rescheduled = bool(post.get('is_rescheduled'))
        notes = (post.get('notes') or '').strip()

        original_loan_id = None
        if is_rescheduled:
            try:
                original_loan_id = int(post.get('original_loan_id') or 0) or None
            except (ValueError, TypeError):
                original_loan_id = None

        if loan_amount <= 0:
            errors['loan_amount'] = 'Loan amount must be greater than zero.'
        if installments <= 0:
            errors['installments'] = 'Number of installments must be greater than zero.'

        if errors:
            return self._loan_apply_render(member, errors=errors, form_data=post)

        vals = {
            'member_id': member.id,
            'loan_amount': loan_amount,
            'installments': installments,
            'other_income': other_income,
            'bank_installment': bank_installment,
            'is_rescheduled': is_rescheduled,
            'notes': notes,
        }
        if original_loan_id:
            owned = member.loan_ids.filtered(
                lambda l: l.id == original_loan_id and l.state == 'active'
            )
            if owned:
                vals['original_loan_id'] = original_loan_id

        try:
            loan = request.env['zain.loan'].sudo().create(vals)
        except (UserError, ValidationError) as e:
            errors['_general'] = str(e)
            return self._loan_apply_render(member, errors=errors, form_data=post)

        return request.redirect('/my/pfs/loans/%d?created=1' % loan.id)

    @http.route('/my/pfs/loans/<int:loan_id>', type='http', auth='user', website=True)
    def portal_pfs_loan_detail(self, loan_id, created=None, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        loan = member.loan_ids.filtered(lambda l: l.id == loan_id)
        if not loan:
            return request.not_found()

        return request.render('zain_pfs.portal_pfs_loan_detail', {
            'member': member,
            'loan': loan,
            'just_created': bool(created),
            'page_name': 'pfs_loans',
        })

    @http.route('/my/pfs/loans/<int:loan_id>/submit', type='http', auth='user',
                website=True, methods=['POST'])
    def portal_pfs_loan_submit(self, loan_id, **post):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        loan = member.loan_ids.filtered(lambda l: l.id == loan_id and l.state == 'draft')
        if loan:
            try:
                loan.sudo().action_submit()
            except (UserError, ValidationError):
                pass
        return request.redirect('/my/pfs/loans/%d' % loan_id)

    @http.route('/my/pfs/loans/<int:loan_id>/cancel', type='http', auth='user',
                website=True, methods=['POST'])
    def portal_pfs_loan_cancel(self, loan_id, **post):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        cancellable = ('draft', 'submitted', 'preparation',
                       'approval_1', 'approval_2', 'pending_payment')
        loan = member.loan_ids.filtered(
            lambda l: l.id == loan_id and l.state in cancellable
        )
        if loan:
            loan.sudo().action_cancel()
        return request.redirect('/my/pfs/loans')

    # ── Withdrawals ──────────────────────────────────────────────────────────

    @http.route('/my/pfs/withdrawals', type='http', auth='user', website=True)
    def portal_pfs_withdrawals(self, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        config = request.env['zain.configuration'].sudo()._get_config()
        months = member.contribution_months
        withdrawals = member.withdrawal_ids.sorted(key=lambda w: w.id, reverse=True)

        # An active withdrawal request blocks new ones
        has_active = member.withdrawal_ids.filtered(
            lambda w: w.state not in ('approved', 'cancelled')
        )
        return request.render('zain_pfs.portal_pfs_withdrawals', {
            'member': member,
            'withdrawals': withdrawals,
            'can_50': (member.state == 'active'
                       and months >= config.withdrawal_50_min_months
                       and member.eligibility_50 > 0),
            'can_75': (member.state == 'active'
                       and months >= config.withdrawal_75_min_months
                       and member.eligibility_75 > 0),
            'has_active_request': bool(has_active),
            'page_name': 'pfs_withdrawals',
        })

    @http.route('/my/pfs/withdrawals/new', type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def portal_pfs_withdrawal_new(self, **post):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        if request.httprequest.method == 'GET':
            return self._withdrawal_apply_render(member)

        # ── POST: validate + create ──────────────────────────────────────────
        errors = {}
        withdrawal_type = post.get('withdrawal_type', '').strip()
        if withdrawal_type not in ('50', '75'):
            errors['withdrawal_type'] = 'Please select a withdrawal type.'

        try:
            requested_amount = float(post.get('requested_amount') or 0)
        except (ValueError, TypeError):
            requested_amount = 0.0
            errors['requested_amount'] = 'Enter a valid number.'

        notes = (post.get('notes') or '').strip()

        if not errors.get('requested_amount') and requested_amount <= 0:
            errors['requested_amount'] = 'Requested amount must be greater than zero.'

        # Check contribution months eligibility
        if not errors.get('withdrawal_type') and withdrawal_type:
            config = request.env['zain.configuration'].sudo()._get_config()
            months = member.contribution_months
            if withdrawal_type == '50' and months < config.withdrawal_50_min_months:
                errors['withdrawal_type'] = (
                    'You need at least %d contribution months for a 50%% withdrawal '
                    '(you have %d).' % (config.withdrawal_50_min_months, months)
                )
            if withdrawal_type == '75' and months < config.withdrawal_75_min_months:
                errors['withdrawal_type'] = (
                    'You need at least %d contribution months for a 75%% withdrawal '
                    '(you have %d).' % (config.withdrawal_75_min_months, months)
                )

        if errors:
            return self._withdrawal_apply_render(member, errors=errors, form_data=post)

        try:
            withdrawal = request.env['zain.withdrawal'].sudo().create({
                'member_id': member.id,
                'withdrawal_type': withdrawal_type,
                'requested_amount': requested_amount,
                'notes': notes,
            })
        except (UserError, ValidationError) as e:
            errors['_general'] = str(e)
            return self._withdrawal_apply_render(member, errors=errors, form_data=post)

        return request.redirect('/my/pfs/withdrawals/%d?created=1' % withdrawal.id)

    @http.route('/my/pfs/withdrawals/<int:withdrawal_id>', type='http', auth='user',
                website=True)
    def portal_pfs_withdrawal_detail(self, withdrawal_id, created=None, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        withdrawal = member.withdrawal_ids.filtered(lambda w: w.id == withdrawal_id)
        if not withdrawal:
            return request.not_found()

        return request.render('zain_pfs.portal_pfs_withdrawal_detail', {
            'member': member,
            'withdrawal': withdrawal,
            'just_created': bool(created),
            'page_name': 'pfs_withdrawals',
        })

    @http.route('/my/pfs/withdrawals/<int:withdrawal_id>/submit', type='http',
                auth='user', website=True, methods=['POST'])
    def portal_pfs_withdrawal_submit(self, withdrawal_id, **post):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        withdrawal = member.withdrawal_ids.filtered(
            lambda w: w.id == withdrawal_id and w.state == 'draft'
        )
        if withdrawal:
            try:
                withdrawal.sudo().action_submit()
            except (UserError, ValidationError):
                pass
        return request.redirect('/my/pfs/withdrawals/%d' % withdrawal_id)

    @http.route('/my/pfs/withdrawals/<int:withdrawal_id>/cancel', type='http',
                auth='user', website=True, methods=['POST'])
    def portal_pfs_withdrawal_cancel(self, withdrawal_id, **post):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        cancellable = ('draft', 'hr_review', 'approval_1', 'approval_2')
        withdrawal = member.withdrawal_ids.filtered(
            lambda w: w.id == withdrawal_id and w.state in cancellable
        )
        if withdrawal:
            withdrawal.sudo().action_cancel()
        return request.redirect('/my/pfs/withdrawals')

    # ── Resignation & Settlement ─────────────────────────────────────────────

    @http.route('/my/pfs/resignation', type='http', auth='user', website=True)
    def portal_pfs_resignation(self, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        resignations = member.resignation_ids.sorted(key=lambda r: r.id, reverse=True)
        return request.render('zain_pfs.portal_pfs_resignation', {
            'member': member,
            'resignations': resignations,
            'page_name': 'pfs_resignation',
        })

    @http.route('/my/pfs/resignation/<int:resignation_id>', type='http', auth='user',
                website=True)
    def portal_pfs_resignation_detail(self, resignation_id, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        resignation = member.resignation_ids.filtered(lambda r: r.id == resignation_id)
        if not resignation:
            return request.not_found()

        return request.render('zain_pfs.portal_pfs_resignation_detail', {
            'member': member,
            'resignation': resignation,
            'page_name': 'pfs_resignation',
        })

    # ── Profit Distribution ──────────────────────────────────────────────────

    @http.route('/my/pfs/profit', type='http', auth='user', website=True)
    def portal_pfs_profit(self, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        # Each posted or approved distribution line the member belongs to
        lines = member.profit_distribution_line_ids.sorted(
            key=lambda l: l.distribution_id.fiscal_year, reverse=True
        )
        return request.render('zain_pfs.portal_pfs_profit', {
            'member': member,
            'lines': lines,
            'page_name': 'pfs_profit',
        })

    @http.route('/my/pfs/profit/<int:distribution_id>', type='http', auth='user',
                website=True)
    def portal_pfs_profit_detail(self, distribution_id, **kwargs):
        member = self._get_portal_member()
        if not member:
            return request.redirect('/my/pfs')

        line = member.profit_distribution_line_ids.filtered(
            lambda l: l.distribution_id.id == distribution_id
        )
        if not line:
            return request.not_found()

        return request.render('zain_pfs.portal_pfs_profit_detail', {
            'member': member,
            'distribution': line.distribution_id,
            'line': line,
            'page_name': 'pfs_profit',
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

    @http.route('/my/pfs/land-loans/<int:loan_id>', type='http', auth='user',
                website=True)
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
