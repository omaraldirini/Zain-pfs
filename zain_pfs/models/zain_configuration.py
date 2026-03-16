from odoo import fields, models


class ZainConfiguration(models.TransientModel):
    """Singleton settings model for Provident Fund business rules.
    Accessed via Settings > Provident Fund.
    """
    _name = 'zain.configuration'
    _description = 'Zain PFS Configuration'

    # Contribution eligibility
    min_contribution_months = fields.Integer(
        string='Min Contribution Months (Loan/Withdrawal)',
        default=36,
        help='Minimum months of contributions required to apply for a loan.',
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
    vesting_tier_1_months = fields.Integer(string='Vesting Tier 1 – Months', default=36)
    vesting_tier_1_percent = fields.Float(string='Vesting Tier 1 – % Entitlement', default=0.6)

    vesting_tier_2_months = fields.Integer(string='Vesting Tier 2 – Months', default=48)
    vesting_tier_2_percent = fields.Float(string='Vesting Tier 2 – % Entitlement', default=0.8)

    vesting_tier_3_months = fields.Integer(string='Vesting Tier 3 – Months', default=60)
    vesting_tier_3_percent = fields.Float(string='Vesting Tier 3 – % Entitlement', default=1.0)

    # Withdrawal lock periods
    withdrawal_lock_loan_years = fields.Integer(
        string='50% Withdrawal Lock Period (Loans, years)',
        default=3,
    )
    withdrawal_lock_cash_years = fields.Integer(
        string='50% Withdrawal Lock Period (Cash, years)',
        default=5,
    )
