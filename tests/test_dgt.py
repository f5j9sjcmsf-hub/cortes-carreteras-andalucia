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
    vehicle_type: str | None = "anyVehicle",
    creation_time: str | None = "2026-08-01T10:15:30.000+02:00",
    version_time: str | None = "2026-08-02T12:00:00.000+02:00",
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
    vehicle_xml = (
        "<sit:forVehiclesWithCharacteristicsOf>"
        f"<com:vehicleType>{vehicle_type}</com:vehicleType>"
        "</sit:forVehiclesWithCharacteristicsOf>"
        if vehicle_type is not None
        else ""
    )
    creation_xml = (
        f"<sit:situationRecordCreationTime>{creation_time}</sit:situationRecordCreationTime>"
        if creation_time is not None
        else ""
    )
    version_xml = (
        f"<sit:situationRecordVersionTime>{version_time}</sit:situationRecordVersionTime>"
        if version_time is not None
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
          {creation_xml}
          {version_xml}
          <sit:validity><sit:validityStatus>{validity}</sit:validityStatus></sit:validity>
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
          {vehicle_xml}
          <sit:roadOrCarriagewayOrLaneManagementType>{management_type}</sit:roadOrCarriagewayOrLaneManagementType>
        </sit:situationRecord>
      </sit:situation>
    """


def _xml(*situations: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <d2:D2LogicalModel
      xmlns:d2="urn:datex:d2"
      xmlns:sit="urn:datex:situation"
      xmlns:com="urn:datex:common"
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
            "published_at": "2026-08-01T10:15:30.000+02:00",
            "source_updated_at": "2026-08-02T12:00:00.000+02:00",
            "province": "Málaga",
            "localities": ["Casares"],
            "road": "A-7150",
            "km_start": 1.2,
            "km_end": 2.25,
            "direction": "both",
            "reason": "Desprendimientos",
            "cause_code": "environmentalObstruction",
            "detail_code": "rockfalls",
            "alternative": "",
        }
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("negative", "decreasing"),
        ("positive", "increasing"),
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
        situation_id="s-increasing",
        record_id="r-increasing",
        direction="positive",
        municipalities=("MANILVA", "CASARES"),
        creation_time="2026-08-01T10:00:00.000+02:00",
    )
    decreasing = _record(
        situation_id="s-decreasing",
        record_id="r-decreasing",
        direction="negative",
        municipalities=("CASARES", "MANILVA"),
        kilometres=("1.2", "2.25"),
        creation_time="2026-08-01T08:30:00.000+00:00",
    )

    closures = dgt.parse_closures(_xml(increasing, decreasing))

    assert len(closures) == 1
    assert closures[0]["direction"] == "both"
    assert closures[0]["localities"] == ["Casares", "Manilva"]
    assert closures[0]["source_ids"] == [
        "s-increasing:r-increasing",
        "s-decreasing:r-decreasing",
    ]
    assert closures[0]["situation_ids"] == ["s-increasing", "s-decreasing"]
    assert closures[0]["record_ids"] == ["r-increasing", "r-decreasing"]
    assert closures[0]["published_at"] == "2026-08-01T10:00:00.000+02:00"


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
        {"vehicle_type": "heavyGoodsVehicle"},
    ],
)
def test_filters_non_matching_records(overrides):
    assert dgt.parse_closures(_xml(_record(**overrides))) == []


def test_accepts_complete_road_closure_when_lane_usage_is_absent():
    closures = dgt.parse_closures(_xml(_record(lane_usage=None)))
    assert len(closures) == 1


def test_published_at_uses_creation_time_not_version_time():
    closure = dgt.parse_closures(
        _xml(
            _record(
                creation_time="2026-07-10T13:01:47.000+02:00",
                version_time="2026-08-17T09:30:00.000+02:00",
            )
        )
    )[0]
    assert closure["published_at"] == "2026-07-10T13:01:47.000+02:00"
    assert closure["source_updated_at"] == "2026-08-17T09:30:00.000+02:00"


def test_missing_creation_time_is_returned_as_empty_string():
    closure = dgt.parse_closures(_xml(_record(creation_time=None)))[0]
    assert closure["published_at"] == ""
    assert closure["source_updated_at"] == "2026-08-02T12:00:00.000+02:00"


def test_a44_complete_carriageway_closure_is_included_regression():
    closure = dgt.parse_closures(
        _xml(
            _record(
                situation_id="20856706",
                record_id="25008029",
                province="JAÉN",
                municipalities=("PEGALAJAR", "CAMBIL"),
                road="A-44",
                kilometres=("55.8", "62.5"),
                direction="negative",
                management_type="carriagewayClosures",
                lane_usage="allLanesCompleteCarriageway",
                cause="roadMaintenance",
                detail_tag="roadMaintenanceType",
                detail="roadworks",
                creation_time="2026-07-10T13:01:47.000+02:00",
            )
        )
    )[0]

    assert closure["road"] == "A-44"
    assert closure["province"] == "Jaén"
    assert closure["localities"] == ["Cambil", "Pegalajar"]
    assert (closure["km_start"], closure["km_end"]) == (55.8, 62.5)
    assert closure["direction"] == "decreasing"
    assert closure["reason"] == "Obras"
    assert closure["published_at"] == "2026-07-10T13:01:47.000+02:00"


def test_a44_partial_companion_records_remain_excluded_regression():
    partial_lane = _record(
        situation_id="20856706",
        record_id="25008030",
        province="JAÉN",
        municipalities=("CAMBIL", "PEGALAJAR"),
        road="A-44",
        kilometres=("62.5", "55.8"),
        direction="positive",
        management_type="laneClosures",
        lane_usage="leftLane",
        cause="roadMaintenance",
        detail_tag="roadMaintenanceType",
        detail="roadworks",
    )
    allowed_tidal_lane = _record(
        situation_id="20856706",
        record_id="25008031",
        province="JAÉN",
        municipalities=("PEGALAJAR", "CAMBIL"),
        road="A-44",
        kilometres=("55.8", "62.5"),
        direction="negative",
        management_type="useOfSpecifiedLanesOrCarriagewaysAllowed",
        lane_usage="tidalFlowLane",
        cause="roadMaintenance",
        detail_tag="roadMaintenanceType",
        detail="roadworks",
    )

    assert dgt.parse_closures(_xml(partial_lane, allowed_tidal_lane)) == []


def test_a44_tidal_lane_is_attached_as_an_alternative_to_complete_closure():
    complete_closure = _record(
        situation_id="22631304",
        record_id="26870209",
        province="JAÉN",
        municipalities=("CÁRCHELES", "PEGALAJAR"),
        road="A-44",
        kilometres=("59.7", "55.8"),
        direction="negative",
        management_type="carriagewayClosures",
        lane_usage="allLanesCompleteCarriageway",
        cause="roadMaintenance",
        detail_tag="roadMaintenanceType",
        detail="roadworks",
    )
    tidal_lane = _record(
        situation_id="22631304",
        record_id="26870211",
        province="JAÉN",
        municipalities=("CÁRCHELES", "PEGALAJAR"),
        road="A-44",
        kilometres=("59.7", "55.8"),
        direction="negative",
        management_type="useOfSpecifiedLanesOrCarriagewaysAllowed",
        lane_usage="tidalFlowLane",
        cause="roadMaintenance",
        detail_tag="roadMaintenanceType",
        detail="roadworks",
    )

    closures = dgt.parse_closures(_xml(complete_closure, tidal_lane))

    assert len(closures) == 1
    assert closures[0]["record_ids"] == ["26870209"]
    assert closures[0]["alternative"] == (
        "Tráfico desviado por un carril reversible habilitado "
        "en la calzada contraria"
    )


def test_a395_single_alternate_line_is_attached_as_an_alternative():
    complete_closure = _record(
        situation_id="22923756",
        record_id="27174129",
        province="GRANADA",
        municipalities=("GÜÉJAR SIERRA",),
        road="A-395",
        kilometres=("23.7",),
        direction="negative",
        management_type="carriagewayClosures",
        lane_usage="allLanesCompleteCarriageway",
        cause="roadMaintenance",
        detail_tag="roadMaintenanceType",
        detail="roadworks",
    )
    alternate_line = _record(
        situation_id="22923756",
        record_id="27174130",
        province="GRANADA",
        municipalities=("GÜÉJAR SIERRA",),
        road="A-395",
        kilometres=("23.7",),
        direction="both",
        management_type="singleAlternateLineTraffic",
        lane_usage="allLanesCompleteCarriageway",
        cause="roadMaintenance",
        detail_tag="roadMaintenanceType",
        detail="roadworks",
    )

    closures = dgt.parse_closures(_xml(complete_closure, alternate_line))

    assert len(closures) == 1
    assert closures[0]["record_ids"] == ["27174129"]
    assert closures[0]["alternative"] == (
        "Paso alternativo regulado por un único carril para ambos sentidos"
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"validity": "suspended"},
        {"lane_usage": "leftLane"},
        {"management_type": "laneClosures"},
        {"vehicle_type": "heavyGoodsVehicle"},
        {"kilometres": ("55.8", "60.0")},
        {"direction": "positive"},
        {"situation_id": "different-situation"},
    ],
)
def test_unverified_or_unrelated_tidal_lane_is_not_announced(changed):
    complete_closure = _record(
        situation_id="shared-situation",
        record_id="closure",
        road="A-44",
        kilometres=("55.8", "59.7"),
        direction="negative",
        management_type="carriagewayClosures",
    )
    alternative_options = {
        "situation_id": "shared-situation",
        "record_id": "alternative",
        "road": "A-44",
        "kilometres": ("55.8", "59.7"),
        "direction": "negative",
        "management_type": "useOfSpecifiedLanesOrCarriagewaysAllowed",
        "lane_usage": "tidalFlowLane",
    }
    alternative_options.update(changed)
    alternative = _record(**alternative_options)

    closures = dgt.parse_closures(_xml(complete_closure, alternative))

    assert len(closures) == 1
    assert closures[0]["alternative"] == ""


def test_a401_jodar_opposite_carriageway_closures_are_grouped_regression():
    positive = _record(
        situation_id="22173663",
        record_id="26393064",
        province="JAÉN",
        municipalities=("ÚBEDA", "ÚBEDA"),
        road="A-401",
        kilometres=("13.2", "12.0"),
        direction="positive",
        management_type="carriagewayClosures",
        cause="roadMaintenance",
        detail_tag="roadMaintenanceType",
        detail="roadworks",
        creation_time="2026-08-06T13:48:48.000+02:00",
    )
    negative = _record(
        situation_id="22173663",
        record_id="26393065",
        province="JAÉN",
        municipalities=("ÚBEDA", "ÚBEDA"),
        road="A-401",
        kilometres=("12.0", "13.2"),
        direction="negative",
        management_type="carriagewayClosures",
        cause="roadMaintenance",
        detail_tag="roadMaintenanceType",
        detail="roadworks",
        creation_time="2026-08-06T13:48:53.000+02:00",
    )

    closures = dgt.parse_closures(_xml(positive, negative))

    assert len(closures) == 1
    assert closures[0]["road"] == "A-401"
    assert closures[0]["localities"] == ["Úbeda"]
    assert closures[0]["direction"] == "both"
    assert closures[0]["record_ids"] == ["26393064", "26393065"]
    assert closures[0]["published_at"] == "2026-08-06T13:48:48.000+02:00"


def test_closure_value_after_nested_other_is_detected_regression():
    closure = dgt.parse_closures(
        _xml(
            _record(
                situation_id="22352607",
                record_id="26579448",
                province="MÁLAGA",
                municipalities=("MÁLAGA",),
                road="MA-20",
                kilometres=("7.0",),
                direction="negative",
                management_type="carriagewayClosures",
                cause="roadOrCarriagewayOrLaneManagement",
                detail_tag="roadOrCarriagewayOrLaneManagementType",
                detail="other",
                creation_time="2026-08-11T00:01:29.000+02:00",
            )
        )
    )[0]

    assert closure["direction"] == "decreasing"
    assert closure["reason"] == "Regulación especial"
    assert closure["source_ids"] == ["22352607:26579448"]


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

