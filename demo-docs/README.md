# IntelliKnow Manual Upload Documents

The original automated-test fixtures are stored in:

`tests/fixtures/docs/`

For a normal manual upload demo, use these fixtures:

| File | Expected intent | Useful test question |
| --- | --- | --- |
| `expense_policy.docx` | Finance | Which form should I submit for travel expenses? What is the daily meal limit? What happens if I lose a receipt? |
| `budget.xlsx` | Finance | What is the FY2026 travel budget? Is total YTD spending over budget? Who approves a purchase above USD 25,000? |
| `project_lantern_approval_policy.docx` | Finance | Does a Project Lantern request need Level-2 approval? Which form is required? |
| `handbook.pdf` | HR | How many annual-leave days do employees receive? When does carried leave expire? How do I request parental leave? |
| `salary_bands.pdf` | HR | What is the Band 6 midpoint? What approvals are needed above a band maximum? How does a promotion affect salary? |
| `nda.docx` | Legal | How quickly must a security incident be reported? How long do confidentiality duties last? Can confidential data be used with public AI services? |
| `salary_nda_vpn_expense_guidance.docx` | Legal | Is VPN expense approval required for a salary NDA? Does signing the NDA grant VPN access? Can an employee purchase a separate VPN? |
| `wrapped_table.docx` | Determined by classifier | What information is in the table? |

Do not use the following as ordinary demo documents. They intentionally exercise failure or edge cases:

- `corrupt.pdf`: invalid PDF content.
- `scanned.pdf`: image-only PDF with no extractable text.
- `duplicate.pdf`: duplicate-content scenario.
- `ragged_salary_grid.pdf`: malformed-table repair scenario.

Additional upload-ready NVIDIA technical documents are in `demo-docs/tech/`.
