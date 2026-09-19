"""Capacity/configuration-standard entries for `rules_report.rules_report`.

Split out of `rules_report.py` to keep that module under the file-length
guideline -- these entries describe the planner's static capacity and
configuration policy (club cap, parallel games, half split, U/JU
separation, configured thresholds, per-team share warnings), a distinct,
self-contained concern from the operational/outcome entries in
`rules_report_operational_rules.py`.
"""

from __future__ import annotations

from typing import Dict, List

from tournament_scheduler import planning_half
from tournament_scheduler.effective_tournament_shape import (
    NO_BYE_EXACT_TEAM_COUNT_BY_AGE_GROUP,
    compute_effective_tournament_shape,
)


def capacity_and_config_rule_entries(planner) -> List[Dict[str, str]]:
    """Return the club-cap, capacity, and configuration-standard entries."""
    entries: List[Dict[str, str]] = []

    entries.append({
        "regel": "Per-klubb kapasitet er et flatt, foretrukket tak",
        "forklaring": (
            f"Planleggeren foretrekker maks {planner.max_club_teams_per_tournament} lag per klubb i én turnering, "
            "uavhengig av hvor mange lag klubben har i aldersgruppen (issue #324). Dette er en sterk "
            "poengsettingsstraff, ikke et hardt forbud -- et tredje eller senere lag fra samme klubb kan "
            "fortsatt velges når ingen andre lovlige kandidater kan fylle turneringen."
        ),
        "kategori": "Automatisk avgjørelse",
    })

    if planner.parallel_games_for_age_group:
        for ag, pg in sorted(planner.parallel_games_for_age_group.items()):
            capacity = planner._max_teams_for(ag)
            configured_rounds = planner.rounds_per_tournament_for_age_group.get(ag)
            shape = compute_effective_tournament_shape(
                ag,
                len(planner.roster.by_age_group(ag)),
                configured_rounds=configured_rounds,
                parallel_game_capacity=pg,
            )
            total_games = shape.effective_round_count * (shape.effective_team_count // 2)
            if shape.input_constrained:
                shape_note = (
                    f" Den registrerte lagpoolen for {ag} har {shape.registered_team_count} lag, færre enn den "
                    f"normale deltakerkapasiteten {shape.preferred_no_bye_team_count}. Planleggeren tilpasser seg "
                    f"til de {shape.effective_team_count} virkelige lagene / {shape.effective_round_count} runder "
                    f"({total_games} kamper) i stedet for å finne opp lag."
                )
            else:
                shape_note = (
                    f" Med full registrert pool spiller turneringen {shape.effective_team_count} lag / "
                    f"{shape.effective_round_count} runder ({total_games} kamper). En turnering som velger færre "
                    "lag enn den fulle registrerte poolen tillater er alltid ugyldig."
                )
                if ag in NO_BYE_EXACT_TEAM_COUNT_BY_AGE_GROUP:
                    shape_note = (
                        f" For {ag} gjelder et eksplisitt krav om nøyaktig {shape.effective_team_count} lag."
                        + shape_note
                    )
                if configured_rounds and shape.effective_round_count < shape.effective_team_count - 1:
                    shape_note += (
                        " Dette er en bevisst begrenset motstanderplan: ikke alle lag møter alle, men hvert lag "
                        "spiller det konfigurerte antallet runder."
                    )
            entries.append({
                "regel": f"Parallelle kamper for {ag}: {pg}",
                "forklaring": (
                    f"For aldersgruppen {ag} spilles det {pg} kamper samtidig per runde. "
                    f"Det gir normal deltakerkapasitet {capacity} lag per turnering ({pg} kamper x 2 lag). "
                    f"Runder per turnering: {configured_rounds or 'full serie'}. "
                    "Når et begrenset rundetall er satt, genereres kampene direkte for dette antallet runder og unngår interne klubboppgjør når det er mulig."
                    + shape_note
                ),
                "kategori": "Hard krav",
            })
    else:
        entries.append({
            "regel": "Parallelle kamper: ikke eksplisitt satt",
            "forklaring": (
                "Ingen aldersgrupper har spesifisert antall parallelle kamper. "
                "Planleggeren bruker da et minimalt teknisk utgangspunkt og utleder kapasiteten fra laglisten."
            ),
            "kategori": "Hard krav",
        })

    entries.append({
        "regel": "Reserverte gjesteplasser teller som kapasitet, ikke som RVV-deltakelse",
        "forklaring": (
            "En turnering kan reservere én eller flere plasser til et gjestelag fra en annen liga/region. "
            "En reservert plass teller med i turneringens kapasitet, rundeantall og istidsform, men "
            "aldri som et RVV-lag: den påvirker ikke deltakelsesmål, hjemmeturneringsfordeling, "
            "reise- eller fairnesstall. En åpen reservering er derfor en bevisst avtalt plass, ikke en "
            "underfylt turnering -- og deltakeroptimalisering/repair kan ikke fylle den med et RVV-lag. "
            "Vertsrepresentasjon krever fortsatt et reelt deltakende lag fra vertsklubben. "
            "Et fylt gjestelag lagres som gjest (ikke et registrert sesonglag) og kampene regenereres når "
            "plassen fylles eller friggis."
        ),
        "kategori": "Hard krav",
    })

    entries.append({
        "regel": "Sesongen deles i to uavhengige planleggingshalvdeler (før/etter jul)",
        "forklaring": (
            f"Nyttårsskillet ({planning_half.half_label('before_christmas')}/"
            f"{planning_half.half_label('after_christmas')}) beregnes én gang som "
            "`christmas_split_date` (1. januar) i planleggingsproblemet og deles av verify_candidate, "
            "score_candidate og Stage 3-søket. En turnering kan flyttes til en ny dato innenfor sin egen "
            "halvdel, men flytting over nyttårsskillet krever et eksplisitt `allow_cross_half_moves`-unntak "
            "og skjer aldri som en bieffekt av optimaliseringssøket."
        ),
        "kategori": "Hard krav",
    })

    entries.append({
        "regel": "U- og JU-kategorier blandes aldri i samme turnering",
        "forklaring": (
            "Aldersgruppen er en eksakt kategori-streng (f.eks. «U10» eller «JU10»), aldri bare "
            "det numeriske alderstallet. Et lags aldersgruppe må være identisk med turneringens "
            "aldersgruppe for at laget kan delta -- «U10» og «JU10» er alltid to forskjellige "
            "turneringer, selv om de deler samme tall. Dette gjelder likt for grunnplanen, "
            "lokalsøket og CP-SAT-søket, og verify_candidate() avviser enhver kandidat med et "
            "`age_group_mismatch`-avvik uansett hvilken motor som produserte den. "
            "`AGE_GROUP_OVERLAP` er en egen regel om datokollisjon mellom nærliggende aldersgrupper "
            "og gir aldri en klubb lov til å sette sammen U- og JU-lag i én turnering."
        ),
        "kategori": "Hard krav",
    })

    fairness_thresholds = planner.fairness_thresholds
    thresholds_text = ", ".join(
        f"{key}={fairness_thresholds[key]}"
        for key in sorted(fairness_thresholds)
    )
    entries.extend([
        {
            "regel": "Konfigurasjonsstandarder og fairness-terskler",
            "forklaring": (
                "Deltakelsesmål utledes fra lagmønsteret og kapasiteten når de ikke er satt eksplisitt, "
                f"og klubb-taket per turnering er flatt {planner.max_club_teams_per_tournament} lag per klubb. "
                f"Fairness-terskler som brukes av fairness-gaten: {thresholds_text}."
            ),
            "kategori": "Konfigurasjonsstandard",
        },
        {
            "regel": f"Standard starttid: {planner.DEFAULT_TOURNAMENT_START_TIME}",
            "forklaring": (
                f"Når en turnering ikke får et mer spesifikt slot-forslag fra hallkalenderen, brukes {planner.DEFAULT_TOURNAMENT_START_TIME} som standard starttid. "
                "Dette er hovedsakelig et teknisk utgangspunkt for planlegging og visning."
            ),
            "kategori": "Konfigurasjonsstandard",
        },
        {
            "regel": f"Buffer mellom turneringer i samme hall-dag: {planner.ARENA_DAY_SEQUENCE_BUFFER_MINUTES} min",
            "forklaring": (
                f"Når flere turneringer havner i samme arena samme dag, legges det inn {planner.ARENA_DAY_SEQUENCE_BUFFER_MINUTES} minutter buffer mellom starttidene. "
                "Dersom sekvensen ikke får plass innen siste gyldige starttid, registreres dette som en hard arenakollisjon i stedet for å klemme starttiden tilbake. "
                "Turneringen havner da i «Må planlegges manuelt»-visningen (manual_schedule.html) for manuell istidsplanlegging."
            ),
            "kategori": "Konfigurasjonsstandard",
        },
        {
            "regel": "Minst mulig gjentatte grupperinger",
            "forklaring": (
                "Når planleggeren velger hvilke lag som skal møtes i en turnering, regnes det ut én samlet score for hver kandidat. "
                "Scoren balanserer klubb-tak, game-count-deficit og gjentatte motstandere, slik at lag som både trenger flere kamper og passer inn i turneringen prioriteres først."
            ),
            "kategori": "Automatisk avgjørelse",
        },
        {
            "regel": "Jevn fordeling av turneringer over sesongen",
            "forklaring": (
                f"Sesongvinduet deles i omtrent like store tidsbolker, og planleggeren gjør i tillegg en global utjevningspass over hele sesongen "
                "før datoene låses. Det gjør at månedslast, overlappende aldersgrupper og gjentatte matchups kan rebalanseres på tvers av grupper, "
                f"i stedet for at hver aldersgruppe bare følger sin egen lokale bucket. Måneder som avviker mer enn {int(planner.max_month_deviation_ratio * 100)}% fra forventet antall turneringer flagges som et varsel."
            ),
            "kategori": "Automatisk avgjørelse",
        },
        {
            "regel": "Rettferdig fordeling av hjemmeturneringer",
            "forklaring": (
                f"Hjemmeturneringer fordeles proporsjonalt etter antall lag hver klubb stiller. Klubber med flere lag får "
                f"hjemmeturnering oftere. Maksimalt tillatt avvik fra forventet antall er {planner.max_hosting_deviation} turnering(er)."
            ),
            "kategori": "Automatisk avgjørelse",
        },
        {
            "regel": "Helgebelastning og feriehelger",
            "forklaring": (
                f"Når flere klubber er aktuelle som vertskap på samme dato, foretrekker planleggeren klubber som ikke har hatt vertskap på forrige helg, "
                f"og som har færre ferie-/helligdagshelger allerede. Fairness-gaten kan slå ut når samme klubb får mer enn {planner.fairness_thresholds.get('max_consecutive_weekend_club_load', 2)} sammenhengende vertskapshelger eller mer enn {planner.fairness_thresholds.get('max_holiday_stretch_club_load', 2)} ferie-/helligdagshelger."
            ),
            "kategori": "Automatisk avgjørelse",
        },
        {
            "regel": "Jevnt antall kamper per lag",
            "forklaring": (
                f"Planleggeren teller opp alle kamper hvert lag spiller i løpet av sesongen. Forskjellen mellom laget med flest "
                f"og færrest kamper skal være maksimalt {planner.max_game_count_spread}. Sesongdekningen vurderes samlet langs hele "
                f"kjeden sesongstart -> første turnering -> ... -> siste turnering -> sesongslutt: lag med et opphold "
                f"(før første turnering, mellom to turneringer, eller etter siste turnering) på mer enn "
                f"{planner.max_early_finish_gap_days} dager flagges som et varsel, ikke bare lag som blir ferdige for tidlig."
            ),
            "kategori": "Automatisk avgjørelse",
        },
        {
            "regel": "Jevn fordeling av kamper innad i aldersgruppe/klubb",
            "forklaring": (
                f"For hver aldersgruppe beregnes gjennomsnittlig antall kamper per lag. Lag som avviker fra dette gjennomsnittet "
                f"med mer enn {planner.max_game_count_spread} kamper flagges som et varsel. Dette fanger opp skjevheter der en klubb "
                "med flere lag i samme aldersgruppe får færre eller flere kamper enn andre lag i samme aldersgruppe."
            ),
            "kategori": "Automatisk avgjørelse",
        },
    ])

    for label, club, age_group, actual, expected in planner._per_team_share_warnings:
        direction = "flere" if actual > expected else "færre"
        entries.append({
            "regel": f"Skjev kampfordeling: {label}",
            "forklaring": (
                f"{label} ({club}, {age_group}) spiller {actual} kamper, mens snittet for {age_group} er {expected:.1f} — "
                f"{abs(actual - expected):.1f} {direction} enn snittet."
            ),
            "kategori": "Anbefaling",
        })

    return entries
