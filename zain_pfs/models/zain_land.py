from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ZainLand(models.Model):
    """Land plot master data managed by the Fund Administrator (BRD §5.3.1)."""
    _name = 'zain.land'
    _description = 'PFS Land Plot'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    name = fields.Char(string='Plot Name / Number', required=True)
    area = fields.Float(string='Area (sq m)')
    basin = fields.Char(string='Basin (Hod)')
    plot_number = fields.Char(string='Official Plot Number')
    price = fields.Float(string='Price (JOD)', required=True, tracking=True)
    location = fields.Char(string='Location')
    notes = fields.Text()

    status = fields.Selection([
        ('available', 'Available'),
        ('reserved', 'Reserved'),
        ('sold', 'Sold'),
    ], default='available', string='Status', tracking=True, required=True)

    reserved_member_id = fields.Many2one(
        'zain.member', string='Reserved By', readonly=True, tracking=True,
    )
    reserved_date = fields.Date(string='Reserved Date', readonly=True)
    sold_date = fields.Date(string='Sold Date', readonly=True)

    # ── Related land loan ─────────────────────────────────────────────────────
    land_loan_ids = fields.One2many('zain.land.loan', 'land_id', string='Land Loans')
    active_loan_id = fields.Many2one(
        'zain.land.loan', string='Active Land Loan',
        compute='_compute_active_loan', store=False,
    )

    _sql_constraints = [
        ('plot_number_uniq', 'UNIQUE(plot_number)',
         'Official Plot Number must be unique.'),
    ]

    # ── Computed ──────────────────────────────────────────────────────────────

    @api.depends('land_loan_ids.state')
    def _compute_active_loan(self):
        for rec in self:
            active = rec.land_loan_ids.filtered(lambda l: l.state == 'active')
            rec.active_loan_id = active[0] if active else False

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_mark_available(self):
        for rec in self:
            active_loans = rec.land_loan_ids.filtered(
                lambda l: l.state in ('active', 'pending_payment')
            )
            if active_loans:
                raise UserError(_(
                    'Cannot mark plot "%s" as available: it has an active loan (%s).'
                ) % (rec.name, active_loans[0].name))
        self.write({
            'status': 'available',
            'reserved_member_id': False,
            'reserved_date': False,
        })

    def action_mark_sold(self):
        self.write({'status': 'sold', 'sold_date': fields.Date.today()})

    def action_reserve(self, member_id):
        """Called from zain.land.loan on disbursement."""
        self.write({
            'status': 'reserved',
            'reserved_member_id': member_id,
            'reserved_date': fields.Date.today(),
        })
