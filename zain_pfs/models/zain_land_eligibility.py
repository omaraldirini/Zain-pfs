from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ZainLandEligibility(models.Model):
    """Sequential eligibility list for land purchase (BRD §5.3.2).

    The Fund Administrator maintains this ordered list.  Members at the
    top of the queue are offered available plots first.  When a member
    selects a plot, their queue entry moves to 'offered'; once the land
    loan is active it moves to 'completed'.
    """
    _name = 'zain.land.eligibility'
    _description = 'PFS Land Purchase Eligibility List'
    _order = 'sequence, id'

    sequence = fields.Integer(
        string='Queue Position', default=10,
        help='Lower number = higher priority in the eligibility queue.',
    )
    member_id = fields.Many2one(
        'zain.member', string='Member', required=True,
        ondelete='restrict', index=True,
    )
    employee_id = fields.Many2one(
        related='member_id.employee_id', readonly=True,
    )
    department_id = fields.Many2one(
        related='member_id.department_id', readonly=True,
    )
    member_contribution_months = fields.Integer(
        related='member_id.contribution_months',
        string='Contribution Months', readonly=True,
    )
    member_balance = fields.Float(
        related='member_id.current_balance',
        string='Available Balance (JOD)', readonly=True,
    )

    state = fields.Selection([
        ('waiting', 'Waiting'),
        ('offered', 'Plot Offered'),
        ('completed', 'Completed'),
        ('removed', 'Removed'),
    ], default='waiting', string='Status', required=True, tracking=True)

    offered_land_id = fields.Many2one(
        'zain.land', string='Offered Plot',
        domain="[('status', '=', 'available')]",
    )
    land_loan_id = fields.Many2one(
        'zain.land.loan', string='Land Loan', readonly=True,
    )
    notes = fields.Text()
    enroll_date = fields.Date(
        string='Enrolment Date', default=fields.Date.today,
    )

    _sql_constraints = [
        ('member_active_uniq',
         "UNIQUE(member_id, state) WHERE state NOT IN ('completed', 'removed')",
         'A member can only appear once in the active eligibility queue.'),
    ]

    def action_offer_plot(self):
        for rec in self:
            if not rec.offered_land_id:
                raise UserError(_('Please select a land plot to offer.'))
            if rec.offered_land_id.status != 'available':
                raise UserError(_(
                    'Plot "%s" is not available (current status: %s).'
                ) % (rec.offered_land_id.name, rec.offered_land_id.status))
        self.write({'state': 'offered'})

    def action_complete(self):
        self.write({'state': 'completed'})

    def action_remove(self):
        self.write({'state': 'removed'})

    def action_reset_waiting(self):
        self.write({'state': 'waiting', 'offered_land_id': False})
