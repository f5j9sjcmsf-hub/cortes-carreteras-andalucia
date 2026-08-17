from __future__ import annotations

from io import BytesIO
import urllib.error

import pytest

from src import dgt


def _record(
    *,
    situation_id: str = "s1",
    record_id: str = "r1",
    province: str = "MÁLAGA",
    community: str | None = "ANDALUCÍA",
    municipalities: tuple[str, ...] = ("CASARES", "CASARES"),
    road: str = "A-7150",
    kilometres: tuple[str, ...] = ("2.25", "1.2"),
    direction: str = "both",
    validity: str = "active",
    management_type: str = "roadClosed",
    lane_usage: str | None = "allLanesCompleteCarriageway",
    cause: str = "environmentalObstruction",
    detail_tag: str = "environmentalObstructionType",
    detail: str = "rockfalls",
) -> str:
    community_xml = (
        f"<lse:autonomousCommunity>{community}</lse:autonomousCommunity>"
        if community is not None
        else ""
    )
    lane_xml = (
        f"<loc:lane><loc:laneUsage>{lane_usage}</loc:laneUsage></loc:lane>"
        if lane_usage is not None
        else ""
    )
    location_values = "".join(
        f"<lse:municipality>{municipality}</lse:municipality>"
        for municipality in municipalities
    )
    kilometre_values = "".join(
        f"<lse:kilometerPoint>{kilometre}</lse:kilometerPoint>"
        for kilometre in kilometres
    )
    return f"""
      <sit:situation id="{situation_id}">
        <sit:situationRecord id="{record_id}">
          <sit:validity><sit:validityStatus>{validity}</sit:validityStatus></sit:validity>
          <sit:roadOrCarriagewayOrLaneManagementType>{management_type}</sit:roadOrCarriagewayOrLaneManagementType>
          <sit:cause>
            <sit:causeType>{cause}</sit:causeType>
            <sit:{detail_tag}>{detail}</sit:{detail_tag}>
          </sit:cause>
          <sit:locationReference>
            <loc:supplementaryPositionalDescription>
              <loc:roadInformation><loc:roadName>{road}</loc:roadName></loc:roadInformation>
              <loc:carriageway>{lane_xml}</loc:carriageway>
            </loc:supplementaryPositionalDescription>
            <loc:tpegLinearLocation>
              <loc:tpegDirection>unrelatedFallback</loc:tpegDirection>
              <lse:tpegDirectionRoad>{direction}</lse:tpegDirectionRoad>
              <loc:from>
                <lse:province>{province}</lse:province>
                {community_xml}
                {location_values}
                {kilometre_values}
              </loc:from>
            </loc:tpegLinearLocation>
          </sit:locationReference>
        </sit:situationRecord>
      </sit:situation>
    """


def _xml(*situations: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <d2:D2LogicalModel
      xmlns:d2="urn:datex:d2"
      xmlns:sit="urn:datex:situation"
      xmlns:loc="urn:datex:location"
      xmlns:lse="urn:dgt:location-extension">
      <d2:payload>{''.join(situations)}</d2:payload>
    </d2:D2LogicalModel>
    """.encode()


def test_extracts_normalised_fields_from_namespaced_xml():
    closures = dgt.parse_closures(_xml(_record()))

    assert closures == [
        {
            "source_ids": ["s1:r1"],
            "situation_ids": ["s1"],
            "record_ids": ["r1"],
            "province": "Málaga",
            "localities": ["Casares"],
            "road": "A-7150",
            "km_start": 1.2,
            "km_end": 2.25,
            "direction": "both",
            "reason": "Desprendimientos",
            "cause_code": "environmentalObstruction",
            "detail_code": "rockfalls",
        }
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("negative", "increasing"),
        ("positive", "decreasing"),
        ("both", "both"),
        ("unknown", "unknown"),
        ("futureDirection", "unknown"),
    ],
)
def test_dgt_direction_mapping(source: str, expected: str):
    closure = dgt.parse_closures(_xml(_record(direction=source)))[0]
    assert closure["direction"] == expected


def test_exact_opposite_directions_are_grouped_and_ids_combined():
    increasing = _record(
        situation_id="s-negative",
        record_id="r-negative",
        direction="negative",
        municipalities=("MANILVA", "CASARES"),
    )
    decreasing = _record(
        situation_id="s-positive",
        record_id="r-positive",
        direction="positive",
        municipalities=("CASARES", "MANILVA"),
        kilometres=("1.2", "2.25"),
    )

    closures = dgt.parse_closures(_xml(increasing, decreasing))

    assert len(closures) == 1
    assert closures[0]["direction"] == "both"
    assert closures[0]["localities"] == ["Casares", "Manilva"]
    assert closures[0]["source_ids"] == [
        "s-negative:r-negative",
        "s-positive:r-positive",
    ]
    assert closures[0]["situation_ids"] == ["s-negative", "s-positive"]
    assert closures[0]["record_ids"] == ["r-negative", "r-positive"]


@pytest.mark.parametrize(
    "changed",
    [
        {"road": "A-377"},
        {"province": "CÁDIZ"},
        {"municipalities": ("ESTEPONA",)},
        {"kilometres": ("1.2", "2.3")},
        {
            "cause": "infrastructureDamageObstruction",
            "detail_tag": "infrastructureDamageType",
            "detail": "damagedRoadSurface",
        },
    ],
)
def test_opposites_with_non_identical_location_or_cause_are_not_grouped(changed):
    first = _record(record_id="r1", direction="negative")
    second = _record(
        situation_id="s2",
        record_id="r2",
        direction="positive",
        **changed,
    )

    assert len(dgt.parse_closures(_xml(first, second))) == 2


def test_native_both_record_is_preserved_and_absorbs_redundant_direction():
    native = _record(record_id="native", direction="both")
    redundant = _record(
        situation_id="s2", record_id="redundant", direction="negative"
    )

    closures = dgt.parse_closures(_xml(native, redundant))

    assert len(closures) == 1
    assert closures[0]["direction"] == "both"
    assert closures[0]["record_ids"] == ["native", "redundant"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"community": "ARAGÓN", "province": "HUESCA"},
        {"validity": "suspended"},
        {"management_type": "laneClosures"},
        {"lane_usage": "rightLane"},
    ],
)
def test_filters_non_matching_records(overrides):
    assert dgt.parse_closures(_xml(_record(**overrides))) == []


def test_accepts_complete_road_closure_when_lane_usage_is_absent():
    closures = dgt.parse_closures(_xml(_record(lane_usage=None)))
    assert len(closures) == 1


def test_uses_andalusian_province_fallback_when_community_is_absent():
    closures = dgt.parse_closures(
        _xml(_record(community=None, province="JAÉN", municipalities=("BAEZA",)))
    )
    assert closures[0]["province"] == "Jaén"
    assert closures[0]["localities"] == ["Baeza"]


def test_missing_and_invalid_kilometres_become_none_without_crashing():
    closure = dgt.parse_closures(
        _xml(_record(kilometres=("no disponible", "NaN")))
    )[0]
    assert closure["km_start"] is None
    assert closure["km_end"] is None


def test_decimal_comma_and_duplicate_kilometres_are_supported():
    closure = dgt.parse_closures(
        _xml(_record(kilometres=("10,50", "9,25", "10,50")))
    )[0]
    assert closure["km_start"] == 9.25
    assert closure["km_end"] == 10.5


@pytest.mark.parametrize(
    ("cause", "tag", "detail", "expected"),
    [
        (
            "infrastructureDamageObstruction",
            "infrastructureDamageType",
            "damagedRoadSurface",
            "Daños en la calzada",
        ),
        ("roadMaintenance", "roadMaintenanceType", "roadworks", "Obras"),
        (
            "weatherRelatedRoadConditions",
            "weatherRelatedRoadConditionType",
            "snowOnRoad",
            "Nieve en la calzada",
        ),
        (
            "environmentalObstruction",
            "environmentalObstructionType",
            "fallenTrees",
            "Árboles caídos",
        ),
        (
            "newGenericCode",
            "obstructionType",
            "newRoadProblem",
            "Otro motivo: new road problem",
        ),
    ],
)
def test_reason_translation_and_readable_fallback(cause, tag, detail, expected):
    closure = dgt.parse_closures(
        _xml(_record(cause=cause, detail_tag=tag, detail=detail))
    )[0]
    assert closure["reason"] == expected
    assert closure["cause_code"] == cause
    assert closure["detail_code"] == detail


def test_invalid_xml_is_an_explicit_error_not_an_empty_feed():
    with pytest.raises(dgt.DGTFeedError, match="XML de la DGT no es válido"):
        dgt.parse_closures(b"<not-closed>")


def test_well_formed_non_datex_response_is_an_explicit_error():
    with pytest.raises(dgt.DGTFeedError, match="estructura de una publicación DATEX II"):
        dgt.parse_closures(b"<html><body>Error del servidor</body></html>")


def test_valid_datex_publication_without_matching_closures_is_empty():
    assert dgt.parse_closures(_xml()) == []


class _Response(BytesIO):
    status = 200

    def getcode(self):
        return self.status


def test_fetch_uses_official_url_and_parses_response(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["user_agent"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return _Response(_xml(_record()))

    monkeypatch.setattr(dgt.urllib.request, "urlopen", fake_urlopen)

    closures = dgt.fetch_closures(timeout=17)

    assert len(closures) == 1
    assert seen == {
        "url": dgt.DGT_FEED_URL,
        "user_agent": "Mozilla/5.0 (compatible; CarreterasCortadasAndaluciaBot/1.0)",
        "timeout": 17,
    }


def test_fetch_does_not_turn_http_error_into_empty_result(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, None)

    monkeypatch.setattr(dgt.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(dgt.DGTFeedError, match="HTTP 503"):
        dgt.fetch_closures()


def test_fetch_does_not_turn_network_error_into_empty_result(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(dgt.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(dgt.DGTFeedError, match="No se pudo descargar"):
        dgt.fetch_closures()


@pytest.mark.parametrize("payload", [b"", b"   ", b"<broken>"])
def test_fetch_rejects_empty_or_malformed_payload(monkeypatch, payload):
    monkeypatch.setattr(
        dgt.urllib.request, "urlopen", lambda request, timeout: _Response(payload)
    )

    with pytest.raises(dgt.DGTFeedError):
        dgt.fetch_closures()

