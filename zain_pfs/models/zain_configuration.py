from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ZainConfiguration(models.Model):
    """Singleton settings model for Provident Fund business rules.
    Only one record may exist. Accessed via _get_config().
    """
    _name = 'zain.configuration'
    _description = 'Zain PFS Configuration'

    # Contribution eligibility
    min_contribution_months = fields.Integer(
        string='Min Contribution Months (Loan/Withdrawal)',
        default=36,
        help='Minimum posted contribution months required to apply for a loan or withdrawal.',
    )
    withdrawal_50_min_months = fields.Integer(
        string='Min Months for 50% Withdrawal',
        default=60,
    )
    withdrawal_75_min_months = fields.Integer(
        string='Min Months for 75% Withdrawal',
        default=240,
        help='240 months = 20 years of service.',
    )

    # Loan rules
    loan_installment_cap = fields.Float(
        string='Loan Installment Cap (%)',
        default=0.5,
        digits=(5, 4),
        help='Max fraction of average income that total loan installments may consume (0.5 = 50%).',
    )
    min_remaining_salary = fields.Float(
        string='Min Remaining Salary after Deductions (JOD)',
        default=240.0,
    )
    loan_admin_fees = fields.Float(
        string='Loan Admin Fees (JOD)',
        default=5.0,
    )
    loan_admin_fees_threshold = fields.Float(
        string='Loan Amount Threshold for Admin Fees (JOD)',
        default=504.0,
        help='Admin fees only apply when the loan amount exceeds this value.',
    )
    reschedule_fees = fields.Float(
        string='Reschedule / Buyout Fees (JOD)',
        default=25.0,
    )

    # Vesting tiers (company contributions + profits on resignation)
    vesting_tier_1_months = fields.Integer(
        string='Vesting Tier 1 – Min Months',
        default=36,
    )
    vesting_tier_1_percent = fields.Float(
        string='Vesting Tier 1 – % Entitlement',
        default=0.6,
        digits=(5, 4),
    )

    vesting_tier_2_months = fields.Integer(
        string='Vesting Tier 2 – Min Months',
        default=48,
    )
    vesting_tier_2_percent = fields.Float(
        string='Vesting Tier 2 – % Entitlement',
        default=0.8,
        digits=(5, 4),
    )

    vesting_tier_3_months = fields.Integer(
        string='Vesting Tier 3 – Min Months (Full Vesting)',
        default=60,
    )
    vesting_tier_3_percent = fields.Float(
        string='Vesting Tier 3 – % Entitlement',
        default=1.0,
        digits=(5, 4),
    )

    # Withdrawal lock periods
    withdrawal_lock_loan_years = fields.Integer(
        string='50% Withdrawal Lock Period – Loan Settlement (years)',
        default=3,
    )
    withdrawal_lock_cash_years = fields.Integer(
        string='50% Withdrawal Lock Period – Cash (years)',
        default=5,
    )

    # ── Singleton enforcement ────────────────────────────────────────────────

    @api.model
    def create(self, vals):
        if self.search([], limit=1):
            raise ValidationError(
                'Only one PFS Configuration record is allowed. '
                'Please edit the existing record instead of creating a new one.'
            )
        return super().create(vals)

    # ── Constraints ──────────────────────────────────────────────────────────

    @api.constrains(
        'vesting_tier_1_months', 'vesting_tier_2_months', 'vesting_tier_3_months',
    )
    def _check_vesting_tier_months(self):
        for rec in self:
            if not (rec.vesting_tier_1_months < rec.vesting_tier_2_months < rec.vesting_tier_3_months):
                raise ValidationError(
                    'Vesting tier months must be strictly ascending: Tier 1 < Tier 2 < Tier 3.'
                )

    @api.constrains(
        'vesting_tier_1_percent', 'vesting_tier_2_percent', 'vesting_tier_3_percent',
    )
    def _check_vesting_tier_percents(self):
        for rec in self:
            for fname, label in [
                ('vesting_tier_1_percent', 'Tier 1'),
                ('vesting_tier_2_percent', 'Tier 2'),
                ('vesting_tier_3_percent', 'Tier 3'),
            ]:
                val = rec[fname]
                if not (0.0 <= val <= 1.0):
                    raise ValidationError(
                        f'Vesting {label} entitlement must be between 0 and 1 (e.g. 0.8 = 80%).'
                    )
            if not (rec.vesting_tier_1_percent <= rec.vesting_tier_2_percent <= rec.vesting_tier_3_percent):
                raise ValidationError(
                    'Vesting tier percentages must be non-decreasing: Tier 1 ≤ Tier 2 ≤ Tier 3.'
                )

    @api.constrains('loan_installment_cap')
    def _check_installment_cap(self):
        for rec in self:
            if not (0.0 < rec.loan_installment_cap <= 1.0):
                raise ValidationError('Loan Installment Cap must be between 0 and 1 (exclusive of 0).')

    @api.constrains(
        'loan_admin_fees', 'loan_admin_fees_threshold', 'reschedule_fees',
        'min_remaining_salary', 'withdrawal_lock_loan_years', 'withdrawal_lock_cash_years',
    )
    def _check_positive_values(self):
        for rec in self:
            checks = [
                (rec.loan_admin_fees, 'Loan Admin Fees'),
                (rec.loan_admin_fees_threshold, 'Loan Amount Threshold for Admin Fees'),
                (rec.reschedule_fees, 'Reschedule / Buyout Fees'),
                (rec.min_remaining_salary, 'Min Remaining Salary'),
            ]
            for val, label in checks:
                if val < 0:
                    raise ValidationError(f'{label} cannot be negative.')
            if rec.withdrawal_lock_loan_years < 0:
                raise ValidationError('Withdrawal Lock Period (Loan Settlement) cannot be negative.')
            if rec.withdrawal_lock_cash_years < 0:
                raise ValidationError('Withdrawal Lock Period (Cash) cannot be negative.')

    # ── Helper ───────────────────────────────────────────────────────────────

    @api.model
    def _get_config(self):
        """Return the singleton configuration record.
        Creates it with defaults if it does not yet exist (e.g. first install).
        """
        config = self.search([], limit=1)
        if not config:
            config = self.create({})
        return config
