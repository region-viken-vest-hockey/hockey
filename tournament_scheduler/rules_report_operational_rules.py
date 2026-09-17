"""Operational/outcome entries for `rules_report.rules_report`.

Split out of `rules_report.py` to keep that module under the file-length
guideline -- these entries describe outcomes of the planner's automatic
decisions (club-cap exceptions, hosting/arena resolution, manual-placement
queues, per-category warning counts), a distinct concern from the static
capacity/configuration entries in `rules_report_capacity_rules.py`.
"""

from __future__ import annotations

from typing import Dict, List


def operational_rule_entries(planner) -> List[Dict[str, str]]:
    """Return the club-cap-exception, hosting/arena, and warning-count entries."""
    configured_rounds = getattr(planner, "rounds_per_tournament_for_age_group", {}) or {}
    limited_age_groups = sorted(
        age_group
        for age_group, rounds in configured_rounds.items()
        if isinstance(rounds, int) and rounds > 0
    )
    if limited_age_groups:
        game_mode_entry = {
            "regel": "Begrenset rundetall: ikke alle mot alle",
            "forklaring": (
                "For aldersgruppene "
                + ", ".join(limited_age_groups)
                + " er det konfigurert et fast antall runder per turnering. Lagene spiller da dette antallet runder "
                "med unike motstandere, ikke en full serie der alle møter alle -- for eksempel 8 lag / 4 parallelle "
                "kamper / 5 runder = 20 kamper og 5 kamper per lag, uten å bruke alle 28 mulige lagpar. "
                "Aldersgrupper uten konfigurert rundetall spiller fortsatt full round-robin."
            ),
            "kategori": "Automatisk avgjørelse",
        }
        same_club_entry = {
            "regel": "Klubb-interne kamper unngås i begrensede runder",
            "forklaring": (
                "Når en turnering spiller et begrenset antall runder, velges kampene slik at klubboppgjør unngås "
                "når et lovlig program uten slike kamper finnes. Bare når et minimum antall klubb-interne kamper er "
                "uunngåelig for det faktiske antallet lag/runder, tillates de. Uten konfigurert rundetall lager "
                "round-robin-genereringen én kamp mellom hvert inviterte lagpar, også innad i samme klubb."
            ),
            "kategori": "Automatisk avgjørelse",
        }
    else:
        game_mode_entry = {
            "regel": "Round-robin: alle mot alle innen turneringen",
            "forklaring": (
                "Innenfor hver turnering spiller alle inviterte lag mot hverandre nøyaktig én gang (round-robin). "
                "Turneringens størrelse og antall parallelle kamper avgjør hvor mange runder som trengs. "
                "Hjemme/borte byttes annenhver runde for rettferdig fordeling."
            ),
            "kategori": "Automatisk avgjørelse",
        }
        same_club_entry = {
            "regel": "Klubb-interne kamper følger round-robin",
            "forklaring": (
                "Hvis flere lag fra samme klubb deltar i samme turnering, behandles de som øvrige deltakere: "
                "round-robin-genereringen lager én kamp mellom hvert inviterte lagpar, også mellom lag fra samme klubb. "
                "Deltakerutvelgelsen forsøker å spre klubber, men dette er et mykt hensyn og ikke et kampfilter."
            ),
            "kategori": "Automatisk avgjørelse",
        }
    return [
        {
            "regel": "Behovsbasert unntak fra klubb-tak per turnering",
            "forklaring": (
                "Når et lag fra en klubb som allerede har fylt det foretrukne klubb-taket (_max_club_teams_for, "
                f"flatt {planner.max_club_teams_per_tournament} lag) er den eneste gjenværende måten å fylle "
                "turneringen lovlig på, kan laget likevel velges — med en sterk straff i prioriteringen "
                "proporsjonal med hvor langt over taket klubben er (issue #324). "
                f"Dette unntaket er brukt {planner._club_cap_overrides} gang(er) i denne sesongplanen."
            ),
            "kategori": "Automatisk avgjørelse",
        },
        {
            "regel": "Ingen overlappende aldersgrupper",
            "forklaring": (
                "Aldersgrupper som deler spillerbase (for eksempel JU11 og U10) skal helst ikke ha turnering samme helg, "
                "fordi noen spillere tilhører begge grupper og ville blitt dobbeltbooket. Planleggeren forsøker å unngå dette; "
                "kollisjoner som ikke kan løses, rapporteres."
            ),
            "kategori": "Automatisk avgjørelse",
        },
        {
            "regel": "Ingen overlappende arenaintervaller",
            "forklaring": (
                "Hver turnering reserverer et fullt dato-/tidsintervall i arenaen, inkludert setup-/byttebuffer per runde og intervaller som går over midnatt. "
                "Senere plasseringer sjekkes mot både skrapede hallbookinger og turneringer som allerede er lagt inn i planen. "
                "Enhver faktisk intervallkollisjon i samme arena er et hardt avvik: den endelige uavhengige verifikasjonen blokkerer normal strict produksjonseksport. "
                "Kollisjonen listes også i «Må planlegges manuelt»-visningen (manual_schedule.html) for operatøroppfølging."
            ),
            "kategori": "Hard krav",
        },
        {
            "regel": "Klubber uten skrapet kalender får sin andel hjemmeturneringer — merket for manuell istidsbooking",
            "forklaring": (
                "Når en klubbs kalenderkilde ikke kan skrapes (blokkert/feilet), får klubben likevel sin "
                "forholdsmessige andel hjemmeturneringer i planen. Siden starttiden ikke kan verifiseres mot "
                "den ekte ishall-kalenderen, merkes disse turneringene (manual_booking_reason) og listes i "
                "«Må planlegges manuelt»-visningen (manual_schedule.html) — istiden må bookes/verifiseres for hånd. "
                "Klubben beholdes i hjemmebanebelastningsberegningen og teller fullt ut i selve planen."
            ),
            "kategori": "Automatisk avgjørelse",
        },
        game_mode_entry,
        same_club_entry,
        {
            "regel": "Klubbbelastning per turnering",
            "forklaring": (
                f"Kjører en advarsel når en klubb har flere lag i en turnering enn det effektive taket tillater; "
                f"{len(planner._club_load_warnings)} tilfelle(r) er registrert i denne planen."
            ),
            "kategori": "Advarsel",
        },
        {
            "regel": "Hjemmeturneringsfordeling",
            "forklaring": (
                f"Kjører en advarsel når en klubb avviker for mye fra proporsjonal hjemmeturneringsfordeling; "
                f"{len(planner._hosting_warnings)} tilfelle(r) er registrert i denne planen."
            ),
            "kategori": "Advarsel",
        },
        {
            "regel": "Klubb x aldersgruppe-dekning av hjemmeturneringer (issue #266)",
            "forklaring": (
                "Hver klubb med minst ett registrert lag i en aldersgruppe skal ha minst én "
                "hjemmeturnering i akkurat den aldersgruppen denne sesongen -- ekstra "
                "hjemmeturneringer i en annen aldersgruppe teller ikke. Når planleggeren ikke "
                "finner en godkjent ledig arenatid for et slikt krav, holdes kravet uløst i "
                "stedet for å stille en annen klubb som vert i stedet, og legges i "
                "«Må planlegges manuelt»-visningen (manual_schedule.html) som "
                f"«MANUAL PLACEMENT REQUIRED». {len(planner._unresolved_hosting_obligations)} "
                "uløst(e) krav er registrert i denne planen"
                + (
                    ": " + "; ".join(
                        f"{item.get('club')} ({item.get('age_group')})"
                        for item in planner._unresolved_hosting_obligations
                    )
                    if planner._unresolved_hosting_obligations
                    else "."
                )
            ),
            "kategori": "Hard krav",
        },
        {
            "regel": "Vertsklubben skal være representert i egen turnering",
            "forklaring": "Når vertsklubben har et registrert lag i aldersgruppen, må minst ett deltakende lag representere "
            "den -- et hardt korrekthetskrav. Delt/felles registrering teller for begge vertsklubbene den består av. "
            "Den endelige verifikasjonen avviser enhver kandidat der kravet brytes.",
            "kategori": "Hard krav",
        },
        {
            "regel": "Kalendertillit er skilt fra vellykket skraping (issue #274)",
            "forklaring": (
                "En klubbs kalenderstatus kan være «known» (nok bevis for automatisk plassering), "
                "«unknown» (blokkert/hoppet over/feilet skraping), eller «untrusted» (skrapingen "
                "lyktes, men klubbens registeroppføring sier at dataene ikke er den reelle, "
                "fullstendige kalenderen -- for eksempel Tønsbergs BookUp-kilde, som i dag kun "
                "returnerer generiske/offentlige plassholderdata). En klubb med «untrusted» eller "
                "«unknown» status beholder sin fulle andel av vertskapsansvaret, men enhver "
                "turnering den er vertskap for må planlegges manuelt inntil et fullstendig, "
                "autentisert kalendersøk er bevist pålitelig."
            ),
            "kategori": "Hard krav",
        },
        {
            "regel": "Delt vertskap for felles klubbregistreringer avgjøres av LLM (issue #274)",
            "forklaring": (
                "En registrering som «Kongsberg/Tønsberg» har mer enn én gyldig fysisk vert. Hvilken "
                "av de to klubbene som skal bære et konkret vertskapsansvar for en aldersgruppe er "
                "en kontekstuell rettferdighetsvurdering -- Python eksponerer kun de deterministiske "
                "fakta (antall vertskap per klubb, kalendertillit per klubb, om automatisk plassering "
                "er mulig), og en LLM/kontroller velger blant registreringens egne deltakerklubber. "
                "Kalenderutilgjengelighet kan aldri i seg selv avgjøre valget; hvis den valgte klubben "
                "mangler en pålitelig automatisk ledig tid, blir turneringen manuell for akkurat den "
                "klubben i stedet for at ansvaret stille overføres til den andre."
            ),
            "kategori": "Hard krav",
        },
        {
            "regel": "Ekstern kalenderkonflikt håndteres ikke-blokkerende",
            "forklaring": (
                "Når en turnerings vertskap har en reell kollisjon med en kjent ekstern "
                "kalenderbooking som verken planleggeren eller optimeringen klarte å unngå "
                "innenfor søkebudsjettet, avvises ikke hele planen -- konflikten legges i "
                "«Må planlegges manuelt»-visningen (manual_schedule.html) som "
                f"«MANUAL PLACEMENT REQUIRED». {len(planner._unresolved_external_conflicts)} "
                "slik(e) konflikt(er) er registrert i denne planen"
                + (
                    ": " + "; ".join(
                        f"{item.get('tournament_id')} ({item.get('host_club')})"
                        for item in planner._unresolved_external_conflicts
                    )
                    if planner._unresolved_external_conflicts
                    else "."
                )
            ),
            "kategori": "Automatisk avgjørelse",
        },
        {
            "regel": "Avvik fra måltall for deltakelse håndteres ikke-blokkerende",
            "forklaring": (
                "Når et lags faktiske antall turneringer avviker fra måltallet (som oftest "
                "fordi det ikke fantes nok ledige turneringsplasser denne sesongen), avvises "
                "ikke hele planen -- avviket er en planleggingskvalitet-avvik, ikke en "
                "istidsoppgave, og rapporteres derfor her i sesongrapporten i stedet for i "
                "«Må planlegges manuelt»-visningen (manual_schedule.html). "
                f"{len(planner._unresolved_participation_shortfalls)} slik(e) avvik er "
                "registrert i denne planen"
                + (
                    ": " + "; ".join(
                        f"{item.get('label')} ({item.get('actual')}/{item.get('target')})"
                        for item in planner._unresolved_participation_shortfalls
                    )
                    if planner._unresolved_participation_shortfalls
                    else "."
                )
            ),
            "kategori": "Automatisk avgjørelse",
        },
        {
            "regel": "Vertskap velges blant deltakerne, ikke omvendt (issue #323)",
            "forklaring": (
                "Vertskap/arena for en turnering velges bare blant klubbene til turneringens "
                "egne deltakere -- deltakerne velges først, deretter avledes de lovlige "
                "vertsklubbene fra dem. Når den ansvarlige/opprinnelige vertsklubben er "
                "representert og fortsatt skylder vertskapsansvar, prøver planleggeren først "
                "avgrensede ansvarsbevarende reparasjoner (andre lovlige datoer/tidsluker for "
                "samme vertsklubb, eller en alternativ lagsammensetning som fortsatt "
                "representerer den). Først når de avgrensede reparasjonsvalgene ikke gir en "
                "verifisert plassering, blir turneringen ikke automatisk plassert hos en "
                "urelatert klubb, deltakerlisten endres ikke for å passe en urelatert arena, "
                "og den legges i «Må planlegges manuelt»-visningen (manual_schedule.html) som "
                "«MANUAL PLACEMENT REQUIRED». Å bytte vertskap til en annen klubb krever en "
                "eksplisitt, validert operatør-/beslutningshandling. "
                f"{len(planner._unresolved_tournament_placements)} "
                "slik(e) uløst(e) plassering(er) er registrert i denne planen"
                + (
                    ": " + "; ".join(
                        f"{item.get('age_group')} ({item.get('date')})"
                        for item in planner._unresolved_tournament_placements
                    )
                    if planner._unresolved_tournament_placements
                    else "."
                )
            ),
            "kategori": "Hard krav",
        },
        {
            "regel": "Kampbalanse og sesongdekning",
            "forklaring": (
                f"Kjører en advarsel når kampantall spres for mye mellom lag eller når et lag har et opphold i sesongdekningen "
                f"(sesongstart -> første turnering -> ... -> siste turnering -> sesongslutt) som overstiger terskelen; "
                f"{len(planner._game_count_warnings)} tilfelle(r) er registrert i denne planen."
            ),
            "kategori": "Advarsel",
        },
        {
            "regel": "Skjev kampfordeling per aldersgruppe",
            "forklaring": (
                f"Kjører en advarsel når et lag avviker for mye fra aldersgruppens forventede kampmengde; "
                f"{len(planner._per_team_share_warnings)} tilfelle(r) er registrert i denne planen."
            ),
            "kategori": "Advarsel",
        },
        {
            "regel": "Feasibility / kapasitet",
            "forklaring": (
                f"Kjører en advarsel når sesongvinduet sannsynligvis ikke kan oppfylle deltakelsesmålet; "
                f"{len(planner._feasibility_warnings)} tilfelle(r) er registrert i denne planen."
            ),
            "kategori": "Advarsel",
        },
        {
            "regel": "Arena-intervallkollisjoner",
            "forklaring": (
                "Kjører en hard validering av fullstendige start-/sluttintervaller i samme arena, ikke bare datoantall; "
                f"{len(getattr(planner, '_arena_day_collisions', []))} tilfelle(r) er registrert i denne planen. "
                "En faktisk kollisjon blokkerer normal strict produksjonseksport og listes samtidig i "
                "«Må planlegges manuelt»-visningen (manual_schedule.html)."
            ),
            "kategori": "Hard krav",
        },
        {
            "regel": "Fallback vertsklubb",
            "forklaring": (
                f"Kjører en advarsel når planleggeren må beholde opprinnelig vertsklubb fordi ønsket slot ikke finnes; "
                f"{len(planner.fallback_host_substitutions)} tilfelle(r) er registrert i denne planen."
            ),
            "kategori": "Advarsel",
        },
        {
            "regel": "Månedslast",
            "forklaring": (
                f"Kjører en advarsel når en måned avviker mer enn terskelen fra forventet turneringslast; "
                f"{len(planner._month_load_warnings)} tilfelle(r) er registrert i denne planen."
            ),
            "kategori": "Advarsel",
        },
    ]
