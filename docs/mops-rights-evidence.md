# MOPS rights evidence

`project_rights_statement` is a pure offline projection of a hash-qualified
MOPS detail response. It first calls the unchanged dividend-statement projector,
then preserves that validated context while adding `rights_fields` under
`mops-rights-statement.v1`.

Only one fully matched `※除權--普通股：` line can state share rights. Earnings
and reserve grants are separately retained as per-thousand literals; their
exact `bonus_per_share` is `(Fraction(Decimal(earnings)) +
Fraction(Decimal(reserves))) / 1000`. Each component is converted before addition,
so a narrow Decimal context cannot round the result. Stock-dividend money, paid
subscription price, quantity, and class stay
separate and are never used as a share-ratio substitute.

Missing, duplicate, blank, and unsupported lines produce explicit unresolved
states with their raw text. The projector neither selects revisions nor infers
historical availability, delivery, cash settlement, or subscription execution.

The observed 2019 form appends one exact empty preferred-share template to the
ordinary line. That literal suffix alone is recognized, with the entire original
line retained. Nonempty preferred templates, repeated suffixes and arbitrary tails
remain unsupported. Blank ordinary quantities still remain blank.
