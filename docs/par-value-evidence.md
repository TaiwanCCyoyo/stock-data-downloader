# Par-value evidence

`shp_utils.par_value_evidence` reads already-held TWSE TWTB8U/TWTB7UDetail and
TPEx pvChgRslt bytes without downloading data or changing a cache.

`extract_par_value_resumes` emits source-bound resume rows. TWSE rows retain an
unresolved exchange ratio until `extract_twse_par_detail` binds one matching
detail response by code, stop date, raw resume hash, and row index. TPEx embeds
its terms in an inert HTML table and supplies the exact ratio directly.

Records preserve literal fields, SHA-256 identities, stop/resume dates, exact
ratio numerator/denominator, old/new face values, and separate previous-close,
reference-price, and auction-base values. Empty responses are `unobserved_empty`;
they do not assert event-free coverage. Historical availability is always null.

The helper rejects malformed schemas, counts, identity/date conflicts, duplicate
events, invalid quantities, and a ratio inconsistent with face values. It never
derives an exchange ratio from rounded prices or emits execution events.
