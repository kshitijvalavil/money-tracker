-- Money Tracker — voice notes (Sprint 16)
--
-- A spoken note like "paid 450 to Starbucks on UPI" becomes a draft the user
-- confirms. Saving one writes an ordinary transaction (source 'voice'), a
-- recurring bill (source 'voice'), or a pending_imports row (source 'voice').
--
-- transactions.source and pending_imports.source are free text, so they already
-- accept 'voice'. Only recurring.source has a CHECK list, so widen it.

alter table public.recurring drop constraint if exists recurring_source_check;
alter table public.recurring
    add constraint recurring_source_check check (source in ('manual', 'detected', 'voice'));
