# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Module

Single Odoo v19 module: `zain_pfs` (Zain Provident Fund System).
Install path: drop `zain_pfs/` into your Odoo addons path and install via Apps.

## Development Commands

```bash
# Install / update the module (adjust path to your odoo-bin)
python odoo-bin -c odoo.conf -u zain_pfs -d <db>

# Install from scratch
python odoo-bin -c odoo.conf -i zain_pfs -d <db>

# Run with debug log for this module only
python odoo-bin -c odoo.conf -u zain_pfs -d <db> --log-level=debug --log-handler=odoo.addons.zain_pfs:DEBUG

# Run tests (requires a test database)
python odoo-bin -c odoo.conf --test-enable -u zain_pfs -d <db_test> --stop-after-init
```

## Architecture

### Models and their purpose

| Model | File | Description |
|---|---|---|
| `zain.configuration` | `models/zain_configuration.py` | Singleton settings (fees, vesting tiers, lock periods, eligibility thresholds). Read by all other models. |
| `zain.member` | `models/zain_member.py` | One record per enrolled employee. Central hub: balance inquiry (driven by `as_of_date`), loan summary, eligibility computation. Links to `hr.employee`. |
| `zain.loan` + `zain.loan.line` | `models/zain_loan.py` | Personal loans. 8-stage workflow (draft→paid). Generates repayment schedule on disbursement. |
| `zain.withdrawal` | `models/zain_withdrawal.py` | 50% and 75% partial withdrawals. 5-stage workflow. Enforces lock-period logic via `_get_locked_50_amount()` on `zain.member`. |
| `zain.land` | `models/zain_land.py` | Land plot master data (available/reserved/sold). |
| `zain.land.loan` + `zain.land.loan.line` | `models/zain_land_loan.py` | Land-specific loans tracked separately from personal loans. Disbursement auto-reserves the plot. |
| `zain.resignation` | `models/zain_resignation.py` | Resignation & vesting settlement. Calculates final settlement using the formula: `(EmpCont) + (CoCont × Vesting%) - Withdrawn + ((Profits/3) + (Profits/3 × 2 × Vesting%)) - Loans`. |
| `zain.profit.distribution` + line | `models/zain_profit_distribution.py` | Annual profit distribution. Each member line holds `avg_monthly_balance`; share % and profit amount are computed from the total. |

### Key design decisions

- **Balance fields on `zain.member` are stubs** (`_compute_balances`). They must be replaced with real aggregations from contribution journal lines or payroll integration. The `as_of_date` field controls the cutoff for all balance-related computations.
- **`zain.configuration` is a `TransientModel` (singleton)**. Access it in code via `self.env['zain.configuration'].search([], limit=1)`. Always provide defaults in case no record exists.
- **Approval delegation** (Treasurer → delegate, Committee Head → Secretary) is not yet implemented; add a `delegated_to` Many2one and check both users in the workflow button guard conditions.
- **Maker-checker for journal entries** (§5.7.1) is not yet implemented. The `action_post` stub on `zain.profit.distribution` is the integration point for `account.move` creation.
- **Field-level encryption** (§6) is not yet implemented. Sensitive fields (e.g., `cheque_number`, financial balances) will need a custom encryption mixin using a Trusted Group key mechanism.

### Security groups (least-privilege order)

`group_pfs_employee` < `group_pfs_hr` < `group_pfs_fund_admin` < `group_pfs_fund_committee` < `group_pfs_system_admin`

Each group implies all groups below it.

### Business rules to keep in mind

- **Loan eligibility**: minimum 36 contribution months (configurable); monthly installment + bank installment ≤ 50% of average income.
- **Admin fees**: 5 JOD if loan > 504 JOD; 25 JOD for reschedule/buyout.
- **50% withdrawal lock**: cash withdrawals lock for 5 years; loan settlements lock for 3 years. Base is the balance at the time of the first withdrawal.
- **Vesting tiers** (on resignation): <36 months → 0%; 36–47 → 60%; 48–59 → 80%; 60+ → 100%. Death = 100%; Fraud = 0%.
- **Contribution cut-off**: if resignation date < 15th of month, that month's contributions are excluded.
- **Profit distribution**: based on average monthly balance over 12 months, not year-end balance.

### Pending / stub areas

1. `zain.member._compute_balances` – wire up to actual contribution/payroll records.
2. `zain.profit.distribution.action_post` – create `account.move` entries (maker-checker).
3. Profit distribution screen fields/formulas – pending the Excel template from Mr. Loay (referenced in BRD §5.6.2).
4. Portal views for employees.
5. Field-level encryption for sensitive data (BRD §6).
6. Approval delegation (Treasurer/Committee Head absence handling).
7. Multi-currency handling (BRD §5.7.3).
