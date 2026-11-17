-- Update-Pakete (E9): öffentlicher Bucket «pi-pakete», Schreiben nur über das Dashboard (Dienstrolle).
-- Läuft auf einer neuen und auf hochgezogenen Datenbanken, auch mehrmals hintereinander (räumt auf).
-- Hochladen, Ändern und Löschen werden wie von Supabase Storage nachgebildet (Befehle auf storage.objects
-- mit der Rolle des Kontos; am echten Projekt ungeprüft).
\set chef  '00000000-0000-0000-0000-000000000001'
\set kunde '00000000-0000-0000-0000-000000000002'
insert into public.admins (user_id) values (:'chef') on conflict do nothing;
create or replace function t.paket_aendern(p_name text) returns bigint language plpgsql as $$
declare n bigint;
begin
  with x as (update storage.objects set metadata = '{"size": 1}' where bucket_id = 'pi-pakete' and name = p_name returning 1)
  select count(*) into n from x;
  return n;
end;
$$;
create or replace function t.paket_loeschen(p_name text) returns bigint language plpgsql as $$
declare n bigint;
begin
  with x as (delete from storage.objects where bucket_id = 'pi-pakete' and name = p_name returning 1) select count(*) into n from x;
  return n;
end;
$$;
grant usage on schema t to service_role;
grant execute on function t.paket_aendern(text), t.paket_loeschen(text), t.gleich(text, anyelement, anyelement) to anon, authenticated, service_role;

-- ---------------------------------------------------------------- Bucket
select t.gleich('Bucket pi-pakete ist öffentlich', (select public from storage.buckets where id = 'pi-pakete'), true);
select t.gleich('Bucket pi-pakete: Grenze 20 MB, kein Typfilter',
                (select file_size_limit || '/' || coalesce(array_to_string(allowed_mime_types, ','), 'alle') from storage.buckets where id = 'pi-pakete'),
                '20971520/alle'::text);
select t.gleich('keine erlaubende Regel nennt pi-pakete',
                (select count(*) from pg_policies where schemaname = 'storage' and tablename = 'objects' and permissive = 'PERMISSIVE'
                   and (coalesce(qual, '') like '%pi-pakete%' or coalesce(with_check, '') like '%pi-pakete%' or policyname like 'pakete%')), 0::bigint);
select t.gleich('einschränkende Regeln für alle Rollen: hochladen, ändern, löschen',
                (select string_agg(cmd || ':' || array_to_string(roles, ','), ' ' order by cmd) from pg_policies
                  where schemaname = 'storage' and tablename = 'objects' and policyname like 'pakete%' and permissive = 'RESTRICTIVE'),
                'DELETE:public INSERT:public UPDATE:public'::text);

-- ---------------------------------------------------------------- Schreiben über die Schnittstelle: nie
\set hoch 'insert into storage.objects (bucket_id, name, owner, metadata) values (''pi-pakete'', %L, %L, ''{}'')'
-- Dashboard (Dienstrolle, hier: postgres) legt eine Freigabe ab
reset role;
delete from storage.objects where bucket_id = 'pi-pakete';
set role service_role;
insert into storage.objects (bucket_id, name, metadata) values ('pi-pakete', 'stabil/freigabe.txt', '{}');
insert into storage.objects (bucket_id, name, metadata) values ('pi-pakete', 'stabil/weg.txt', '{}');
select t.gleich('Dienstrolle (Dashboard): hochladen, ändern, löschen geht', t.paket_aendern('stabil/freigabe.txt') + t.paket_loeschen('stabil/weg.txt'), 2::bigint);
select t.als('');
select t.scheitert('nicht angemeldet: kein Hochladen', format(:'hoch', 'stabil/solarmaa-dash-9.9.9.tar.gz', null), '42501');
select t.gleich('nicht angemeldet: kein Ändern, kein Löschen',
                t.paket_aendern('stabil/freigabe.txt') + t.paket_loeschen('stabil/freigabe.txt'), 0::bigint);
select t.als(:'kunde', 'aal2');
select t.scheitert('Kunde: kein Hochladen', format(:'hoch', 'stabil/freigabe2.txt', :'kunde'), '42501');
select t.als(:'chef', 'aal2');
select t.scheitert('Solarmaa mit Zwei-Faktor: kein Hochladen über die Schnittstelle', format(:'hoch', 'test/freigabe.txt', :'chef'), '42501');
select t.gleich('Solarmaa mit Zwei-Faktor: kein Ändern, kein Löschen',
                t.paket_aendern('stabil/freigabe.txt') + t.paket_loeschen('stabil/freigabe.txt'), 0::bigint);

-- Auch eine zu weit gefasste Regel öffnet das Schreiben nicht
reset role;
drop policy if exists test_pakete_alles on storage.objects;
create policy test_pakete_alles on storage.objects for all to public using (true) with check (true);
select t.als('');
select t.scheitert('alles erlaubt: nicht angemeldet lädt nichts hoch', format(:'hoch', 'stabil/solarmaa-dash-9.9.9.tar.gz', null), '42501');
select t.gleich('alles erlaubt: nicht angemeldet ändert und löscht nichts',
                t.paket_aendern('stabil/freigabe.txt') + t.paket_loeschen('stabil/freigabe.txt'), 0::bigint);
select t.als(:'chef', 'aal2');
select t.scheitert('alles erlaubt: Solarmaa lädt nichts hoch', format(:'hoch', 'test/freigabe.txt', :'chef'), '42501');
select t.gleich('alles erlaubt: Solarmaa ändert und löscht nichts',
                t.paket_aendern('stabil/freigabe.txt') + t.paket_loeschen('stabil/freigabe.txt'), 0::bigint);
reset role;
insert into storage.buckets (id, name) values ('anderer_pakete', 'anderer_pakete') on conflict do nothing;
insert into storage.objects (bucket_id, name, metadata) values ('anderer_pakete', 'x.txt', '{}') on conflict do nothing;
select t.als(:'chef', 'aal2');
select t.scheitert('alles erlaubt: Datei aus anderem Bucket nicht nach pi-pakete verschiebbar',
                   $$update storage.objects set bucket_id = 'pi-pakete', name = 'stabil/x.txt' where bucket_id = 'anderer_pakete'$$, '42501');
select t.gleich('andere Buckets: die Regeln für pi-pakete greifen dort nicht',
                (select count(*) from storage.objects where bucket_id = 'anderer_pakete'), 1::bigint);
reset role;
drop policy test_pakete_alles on storage.objects;
delete from storage.objects where bucket_id in ('pi-pakete', 'anderer_pakete');
delete from storage.buckets where id = 'anderer_pakete';
