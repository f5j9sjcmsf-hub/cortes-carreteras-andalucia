"""Download and parse complete road closures from the DGT DATEX II feed.

The DGT profile uses several XML namespaces.  This module deliberately matches
elements by local name instead of by prefix: prefixes are serialization details
and have changed between DATEX releases.
"""

from __future__ import annotations

from collections import defaultdict
import math
import re
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Iterable


DGT_FEED_URL = (
    "https://nap.dgt.es/datex2/v3/dgt/SituationPublication/datex2_v37.xml"
)


class DGTFeedError(RuntimeError):
    """The DGT feed could not be downloaded or parsed safely."""


DIRECTION_MAP = {
    # In the DGT profile, "negative" follows increasing kilometre posts and
    # "positive" follows decreasing kilometre posts.
    "negative": "increasing",
    "positive": "decreasing",
    "both": "both",
    "bothways": "both",
    "unknown": "unknown",
}


GENERIC_REASON_MAP = {
    "abnormalTraffic": "Tráfico anormal",
    "accident": "Accidente",
    "activity": "Actividad en la vía",
    "animalPresenceObstruction": "Animales en la calzada",
    "constructionWork": "Obras",
    "environmentalObstruction": "Obstáculo ambiental",
    "equipmentOrSystemFault": "Avería de equipos o sistemas",
    "generalObstruction": "Obstáculo en la vía",
    "infrastructureDamageObstruction": "Daños en la infraestructura",
    "maintenanceWork": "Trabajos de mantenimiento",
    "nonWeatherRelatedRoadConditions": "Estado deficiente de la vía",
    "poorEnvironmentConditions": "Condiciones ambientales adversas",
    "publicEvent": "Evento público",
    "roadMaintenance": "Obras o mantenimiento",
    "roadworks": "Obras",
    "vehicleObstruction": "Vehículo obstaculizando la vía",
    "weatherRelatedRoadConditions": "Vialidad invernal o meteorología adversa",
}


# The feed normally supplies a generic ``causeType`` and one of the more
# specific enum fields below.  Keep this table intentionally broad so the bot
# can display DGT's usual causes without leaking camelCase identifiers to users.
DETAIL_REASON_MAP = {
    # Environmental obstructions
    "avalanches": "Avalanchas",
    "earthquakeDamage": "Daños por terremoto",
    "fallenTrees": "Árboles caídos",
    "fallingIce": "Caída de hielo",
    "flashFloods": "Inundación repentina",
    "flooding": "Inundación",
    "forestFire": "Incendio forestal",
    "grassFire": "Incendio de vegetación",
    "landslips": "Desprendimientos de tierra",
    "mudSlide": "Corrimiento de barro",
    "rockfalls": "Desprendimientos",
    "seriousFire": "Incendio",
    "smokeOrFumes": "Humo o gases",
    "stormDamage": "Daños por tormenta",
    "subsidence": "Hundimiento del terreno",
    # Infrastructure damage
    "burstPipe": "Rotura de tubería",
    "burstWaterMain": "Rotura de conducción de agua",
    "collapsedSewer": "Alcantarillado colapsado",
    "damagedBridge": "Puente dañado",
    "damagedCrashBarrier": "Barrera de seguridad dañada",
    "damagedFlyover": "Paso elevado dañado",
    "damagedGallery": "Galería dañada",
    "damagedGantry": "Pórtico dañado",
    "damagedRoadSurface": "Daños en la calzada",
    "damagedTunnel": "Túnel dañado",
    "damagedViaduct": "Viaducto dañado",
    "fallenPowerCables": "Cables eléctricos caídos",
    "gasLeak": "Fuga de gas",
    "weakBridge": "Puente con daños estructurales",
    # Road maintenance and construction
    "blastingWork": "Voladuras",
    "bridgeDemolitionWork": "Demolición de puente",
    "bridgeMaintenance": "Mantenimiento de puente",
    "clearanceWork": "Trabajos de limpieza",
    "constructionWork": "Obras de construcción",
    "demolitionWork": "Trabajos de demolición",
    "emergencyMaintenance": "Mantenimiento de emergencia",
    "grassCuttingWork": "Desbroce",
    "inspectionWork": "Trabajos de inspección",
    "litterClearance": "Limpieza de la vía",
    "maintenanceWork": "Trabajos de mantenimiento",
    "overheadWorks": "Trabajos aéreos",
    "repairWork": "Trabajos de reparación",
    "resurfacingWork": "Reasfaltado",
    "roadConstruction": "Construcción de carretera",
    "roadMaintenance": "Mantenimiento de la vía",
    "roadMarkingWork": "Trabajos de señalización horizontal",
    "roadsideWork": "Trabajos en el margen de la vía",
    "roadWideningWork": "Ampliación de la carretera",
    "roadworks": "Obras",
    "saltingInProgress": "Tratamiento preventivo con sal",
    "snowPloughsInUse": "Quitanieves trabajando",
    # Weather-related and winter road conditions
    "blackIce": "Hielo negro",
    "deepSnow": "Nieve profunda",
    "freezingOfWetRoads": "Congelación de la calzada mojada",
    "freezingPavements": "Calzada helada",
    "freshSnow": "Nieve reciente",
    "ice": "Hielo",
    "iceBuildUp": "Acumulación de hielo",
    "icyPatches": "Placas de hielo",
    "looseSnow": "Nieve suelta",
    "packedSnow": "Nieve compactada",
    "roadSurfaceMelting": "Deshielo de la calzada",
    "slushOnRoad": "Nieve fundente en la calzada",
    "snowDrifts": "Ventisqueros",
    "snowOnRoad": "Nieve en la calzada",
    "surfaceWater": "Agua en la calzada",
    "wetAndIcyRoad": "Calzada mojada y helada",
    "wetRoad": "Calzada mojada",
    # Other road-surface conditions
    "dieselOnRoad": "Gasóleo en la calzada",
    "leavesOnRoad": "Hojas en la calzada",
    "looseChippings": "Gravilla suelta",
    "looseSandOnRoad": "Arena en la calzada",
    "mudOnRoad": "Barro en la calzada",
    "oilOnRoad": "Aceite en la calzada",
    "petrolOnRoad": "Combustible en la calzada",
    "roadSurfaceInPoorCondition": "Calzada en mal estado",
    "slipperyRoad": "Calzada deslizante",
    "spillageOnRoad": "Vertido en la calzada",
    # General obstructions
    "airCrash": "Accidente aéreo",
    "childrenOnRoadway": "Menores en la calzada",
    "craneOperating": "Grúa trabajando",
    "cyclistsOnRoadway": "Ciclistas en la calzada",
    "debris": "Restos en la calzada",
    "explosion": "Explosión",
    "explosionHazard": "Riesgo de explosión",
    "hazard": "Peligro en la vía",
    "incident": "Incidencia",
    "objectOnTheRoad": "Objeto en la calzada",
    "objectsFallingFromMovingVehicle": "Caída de objetos de un vehículo",
    "obstructionOnTheRoad": "Obstáculo en la calzada",
    "peopleOnRoadway": "Personas en la calzada",
    "railCrash": "Accidente ferroviario",
    "rescueAndRecoveryWork": "Trabajos de rescate y recuperación",
    "shedLoad": "Carga caída",
    "spillageOnTheRoad": "Vertido en la calzada",
    "unidentifiedObjectOnTheRoad": "Objeto sin identificar en la calzada",
    # Animals
    "animalsOnTheRoad": "Animales en la calzada",
    "herdOfAnimalsOnTheRoad": "Rebaño en la calzada",
    "largeAnimalsOnTheRoad": "Animales grandes en la calzada",
    "smallAnimalsOnTheRoad": "Animales pequeños en la calzada",
    # Vehicle obstructions
    "abnormalLoad": "Transporte especial",
    "brokenDownBus": "Autobús averiado",
    "brokenDownHeavyLorry": "Camión averiado",
    "brokenDownVehicle": "Vehículo averiado",
    "convoy": "Convoy",
    "damagedVehicle": "Vehículo siniestrado",
    "dangerousSlowMovingVehicle": "Vehículo lento peligroso",
    "emergencyVehicle": "Vehículo de emergencia",
    "highSpeedEmergencyVehicle": "Vehículo de emergencia en circulación",
    "longLoad": "Carga de gran longitud",
    "militaryConvoy": "Convoy militar",
    "overheightVehicle": "Vehículo con exceso de altura",
    "prohibitedVehicleOnTheRoad": "Vehículo no autorizado en la vía",
    "slowMovingMaintenanceVehicle": "Vehículo de mantenimiento lento",
    "slowVehicle": "Vehículo lento",
    "trackLayingVehicle": "Vehículo oruga",
    "unlitVehicleOnTheRoad": "Vehículo sin iluminación",
    "vehicleCarryingHazardousMaterials": "Vehículo con mercancías peligrosas",
    "vehicleInDifficulty": "Vehículo con dificultades",
    "vehicleOnFire": "Vehículo incendiado",
    "vehicleOnWrongCarriageway": "Vehículo en sentido contrario",
    "winterMaintenanceVehicle": "Vehículo de vialidad invernal",
    # Accidents
    "accident": "Accidente",
    "accidentInvolvingBicycles": "Accidente con bicicletas",
    "accidentInvolvingBuses": "Accidente con autobús",
    "accidentInvolvingHazardousMaterials": "Accidente con mercancías peligrosas",
    "accidentInvolvingHeavyLorries": "Accidente con vehículos pesados",
    "accidentInvolvingMassTransitVehicle": "Accidente con transporte colectivo",
    "accidentInvolvingMopeds": "Accidente con ciclomotores",
    "accidentInvolvingMotorcycles": "Accidente con motocicletas",
    "accidentInvolvingRadioactiveMaterial": "Accidente con material radiactivo",
    "accidentInvolvingTrain": "Accidente con tren",
    "collision": "Colisión",
    "headOnCollision": "Colisión frontal",
    "headOnOrSideCollision": "Colisión frontal o lateral",
    "jackknifedArticulatedLorry": "Camión articulado atravesado",
    "jackknifedCaravan": "Caravana atravesada",
    "jackknifedTrailer": "Remolque atravesado",
    "multipleVehicleCollision": "Colisión múltiple",
    "multivehicleAccident": "Accidente múltiple",
    "overturnedHeavyLorry": "Camión volcado",
    "overturnedTrailer": "Remolque volcado",
    "overturnedVehicle": "Vehículo volcado",
    "rearCollision": "Colisión por alcance",
    "secondaryAccident": "Accidente secundario",
    "sideCollision": "Colisión lateral",
    "vehicleOffRoad": "Salida de vía",
    "vehicleSpunAround": "Vehículo cruzado en la calzada",
    # Poor environmental conditions
    "badWeather": "Meteorología adversa",
    "denseFog": "Niebla densa",
    "fog": "Niebla",
    "freezingFog": "Niebla engelante",
    "hail": "Granizo",
    "heavyRain": "Lluvia intensa",
    "heavySnow": "Nevada intensa",
    "heavySnowfall": "Nevada intensa",
    "hurricaneForceWinds": "Viento huracanado",
    "lowSunGlare": "Deslumbramiento solar",
    "mist": "Neblina",
    "patchyFog": "Bancos de niebla",
    "precipitationInTheArea": "Precipitaciones",
    "rain": "Lluvia",
    "sandStorms": "Tormenta de arena",
    "severeExhaustPollution": "Contaminación intensa",
    "severeSmog": "Esmog intenso",
    "sleet": "Aguanieve",
    "smokeHazard": "Humo",
    "snowfall": "Nevada",
    "sprayHazard": "Proyecciones de agua",
    "stormForceWinds": "Viento de fuerza temporal",
    "strongGustsOfWind": "Rachas fuertes de viento",
    "strongWinds": "Viento fuerte",
    "temperatureFalling": "Descenso de temperatura",
    "thunderstorm": "Tormenta eléctrica",
    "tornadoes": "Tornados",
    "veryStrongGustsOfWind": "Rachas muy fuertes de viento",
    "visibilityReduced": "Visibilidad reducida",
    "whiteOut": "Visibilidad nula por nieve",
    "winterStorm": "Temporal invernal",
    # Public events
    "agriculturalShow": "Feria agrícola",
    "airShow": "Exhibición aérea",
    "athleticsMeeting": "Prueba de atletismo",
    "bicycleRace": "Carrera ciclista",
    "bullFight": "Festejo taurino",
    "carnival": "Carnaval",
    "concert": "Concierto",
    "exhibition": "Exposición",
    "fair": "Feria",
    "festival": "Festival",
    "filmTVMaking": "Rodaje",
    "footballMatch": "Partido de fútbol",
    "funeral": "Cortejo fúnebre",
    "horseRaceMeeting": "Prueba hípica",
    "majorEvent": "Evento multitudinario",
    "marathon": "Maratón",
    "market": "Mercado",
    "motorcycleRace": "Carrera de motocicletas",
    "motorSportRaceMeeting": "Prueba de motor",
    "parade": "Desfile",
    "procession": "Procesión",
    "publicEvent": "Evento público",
    "raceMeeting": "Competición",
    "show": "Espectáculo",
    "soccerMatch": "Partido de fútbol",
    "stateOccasion": "Acto oficial",
    "tradeFair": "Feria comercial",
    "winterSportsMeeting": "Prueba de deportes de invierno",
    "other": "Otro motivo",
}


DETAIL_TAGS = (
    "roadMaintenanceType",
    "environmentalObstructionType",
    "infrastructureDamageType",
    "weatherRelatedRoadConditionType",
    "nonWeatherRelatedRoadConditionType",
    "vehicleObstructionType",
    "animalPresenceType",
    "generalObstructionType",
    "obstructionType",
    "accidentType",
    "poorEnvironmentType",
    "constructionWorkType",
    "roadworksType",
    "publicEventType",
    "abnormalTrafficType",
    "equipmentOrSystemFaultType",
    "activityType",
)


DETAIL_TAG_CAUSE = {
    "roadMaintenanceType": "roadMaintenance",
    "environmentalObstructionType": "environmentalObstruction",
    "infrastructureDamageType": "infrastructureDamageObstruction",
    "weatherRelatedRoadConditionType": "weatherRelatedRoadConditions",
    "nonWeatherRelatedRoadConditionType": "nonWeatherRelatedRoadConditions",
    "vehicleObstructionType": "vehicleObstruction",
    "animalPresenceType": "animalPresenceObstruction",
    "generalObstructionType": "generalObstruction",
    "obstructionType": "generalObstruction",
    "accidentType": "accident",
    "poorEnvironmentType": "poorEnvironmentConditions",
    "constructionWorkType": "constructionWork",
    "roadworksType": "roadworks",
    "publicEventType": "publicEvent",
    "abnormalTrafficType": "abnormalTraffic",
    "equipmentOrSystemFaultType": "equipmentOrSystemFault",
    "activityType": "activity",
}


ANDALUSIAN_PROVINCES = {
    "almeria": "Almería",
    "cadiz": "Cádiz",
    "cordoba": "Córdoba",
    "granada": "Granada",
    "huelva": "Huelva",
    "jaen": "Jaén",
    "malaga": "Málaga",
    "sevilla": "Sevilla",
}


_LOWERCASE_NAME_WORDS = {"de", "del", "la", "las", "los", "el", "y"}


def _local_name(name: str) -> str:
    """Return an XML element/attribute name without namespace or prefix."""

    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _texts(element: ET.Element, name: str) -> list[str]:
    return [
        text
        for child in element.iter()
        if _local_name(child.tag) == name
        if (text := _clean_text(child.text))
    ]


def _first_text(element: ET.Element, *names: str) -> str:
    for name in names:
        values = _texts(element, name)
        if values:
            return values[0]
    return ""


def _attribute(element: ET.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return _clean_text(value)
    return ""


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _display_name(value: str) -> str:
    value = _clean_text(value)
    if not value or not (value.isupper() or value.islower()):
        return value

    words = value.casefold().title().split()
    return " ".join(
        word.lower() if index and word.casefold() in _LOWERCASE_NAME_WORDS else word
        for index, word in enumerate(words)
    )


def _unique(values: Iterable[str], *, display_names: bool = False) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = _display_name(raw_value) if display_names else _clean_text(raw_value)
        key = _fold(value)
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _canonical_province(values: Iterable[str]) -> str:
    unique = _unique(values, display_names=True)
    if not unique:
        return ""
    return ANDALUSIAN_PROVINCES.get(_fold(unique[0]), unique[0])


def _is_andalusian(record: ET.Element, province_values: list[str]) -> bool:
    communities = _texts(record, "autonomousCommunity")
    if communities:
        return any("andalucia" in _fold(community) for community in communities)
    return any(_fold(province) in ANDALUSIAN_PROVINCES for province in province_values)


def _parse_kilometre(value: str) -> float | None:
    compact = value.strip().replace(" ", "")
    if not compact:
        return None

    # Accept both the feed's decimal point and the decimal comma often used in
    # hand-written fixtures or emergency updates.
    if "," in compact and "." in compact:
        if compact.rfind(",") > compact.rfind("."):
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", "")
    elif "," in compact:
        compact = compact.replace(",", ".")

    try:
        number = float(compact)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _kilometres(record: ET.Element) -> tuple[float | None, float | None]:
    points = {
        number
        for raw_value in _texts(record, "kilometerPoint")
        if (number := _parse_kilometre(raw_value)) is not None
    }
    if not points:
        return None, None
    ordered = sorted(points)
    return ordered[0], ordered[-1]


def _cause(record: ET.Element) -> tuple[str, str]:
    cause_code = _first_text(record, "causeType")
    for tag in DETAIL_TAGS:
        detail_code = _first_text(record, tag)
        if detail_code:
            return cause_code or DETAIL_TAG_CAUSE[tag], detail_code
    return cause_code, ""


_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-záéíóúüñ0-9])(?=[A-ZÁÉÍÓÚÜÑ])")


def _humanize_code(code: str) -> str:
    words = _CAMEL_CASE_BOUNDARY.sub(" ", code).replace("_", " ").replace("-", " ")
    return _clean_text(words).casefold()


def _reason(cause_code: str, detail_code: str) -> str:
    if detail_code in DETAIL_REASON_MAP:
        return DETAIL_REASON_MAP[detail_code]
    if detail_code:
        return f"Otro motivo: {_humanize_code(detail_code)}"
    if cause_code in GENERIC_REASON_MAP:
        return GENERIC_REASON_MAP[cause_code]
    if cause_code:
        return f"Otro motivo: {_humanize_code(cause_code)}"
    return "Motivo no especificado"


def _direction(record: ET.Element) -> str:
    source_value = _first_text(record, "tpegDirectionRoad", "tpegDirection")
    return DIRECTION_MAP.get(source_value.casefold(), "unknown")


def _source_id(situation_id: str, record_id: str) -> str:
    if situation_id and record_id:
        return f"{situation_id}:{record_id}"
    return record_id or situation_id


def _record_to_closure(
    situation: ET.Element,
    record: ET.Element,
) -> dict[str, Any] | None:
    if _first_text(record, "validityStatus").casefold() != "active":
        return None
    if (
        _first_text(record, "roadOrCarriagewayOrLaneManagementType").casefold()
        != "roadclosed"
    ):
        return None

    lane_usages = _texts(record, "laneUsage")
    if lane_usages and not all(
        value.casefold() == "alllanescompletecarriageway" for value in lane_usages
    ):
        return None

    province_values = _texts(record, "province")
    if not _is_andalusian(record, province_values):
        return None

    situation_id = _attribute(situation, "id")
    record_id = _attribute(record, "id")
    source_id = _source_id(situation_id, record_id)
    cause_code, detail_code = _cause(record)
    km_start, km_end = _kilometres(record)
    localities = sorted(
        _unique(_texts(record, "municipality"), display_names=True),
        key=_fold,
    )

    return {
        "source_ids": [source_id] if source_id else [],
        "situation_ids": [situation_id] if situation_id else [],
        "record_ids": [record_id] if record_id else [],
        "province": _canonical_province(province_values),
        "localities": localities,
        "road": _first_text(record, "roadName", "roadNumber", "roadIdentifier"),
        "km_start": km_start,
        "km_end": km_end,
        "direction": _direction(record),
        "reason": _reason(cause_code, detail_code),
        "cause_code": cause_code,
        "detail_code": detail_code,
    }


def _identity(closure: dict[str, Any]) -> tuple[Any, ...]:
    """Identity used only to join exact opposite-direction DGT records."""

    return (
        _fold(closure["province"]),
        tuple(_fold(value) for value in closure["localities"]),
        _fold(closure["road"]),
        closure["km_start"],
        closure["km_end"],
        closure["cause_code"].casefold(),
        closure["detail_code"].casefold(),
    )


def _merge(records: list[dict[str, Any]], direction: str) -> dict[str, Any]:
    merged = dict(records[0])
    merged["direction"] = direction
    for field in ("source_ids", "situation_ids", "record_ids"):
        merged[field] = _unique(
            value for record in records for value in record[field]
        )
    return merged


def _group_opposite_directions(
    closures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    group_order: list[tuple[Any, ...]] = []
    for closure in closures:
        key = _identity(closure)
        if key not in groups:
            group_order.append(key)
        groups[key].append(closure)

    result: list[dict[str, Any]] = []
    for key in group_order:
        by_direction: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for closure in groups[key]:
            by_direction[closure["direction"]].append(closure)

        # A native two-way record already represents the whole closure.  Fold
        # redundant one-way records into it, but leave unknown-direction data
        # separate because it does not prove an affected direction.
        if by_direction["both"]:
            records = (
                by_direction.pop("both")
                + by_direction.pop("increasing", [])
                + by_direction.pop("decreasing", [])
            )
            result.append(_merge(records, "both"))
        elif by_direction["increasing"] and by_direction["decreasing"]:
            records = by_direction.pop("increasing") + by_direction.pop("decreasing")
            result.append(_merge(records, "both"))

        for direction in ("increasing", "decreasing", "unknown"):
            if by_direction[direction]:
                result.append(_merge(by_direction[direction], direction))

    direction_order = {"both": 0, "increasing": 1, "decreasing": 2, "unknown": 3}
    return sorted(
        result,
        key=lambda closure: (
            _fold(closure["province"]),
            _fold(closure["road"]),
            math.inf if closure["km_start"] is None else closure["km_start"],
            math.inf if closure["km_end"] is None else closure["km_end"],
            tuple(_fold(value) for value in closure["localities"]),
            direction_order[closure["direction"]],
        ),
    )


def parse_closures(xml_data: bytes | str) -> list[dict[str, Any]]:
    """Parse DGT DATEX II XML and return active complete Andalusian closures.

    Valid XML with no matching closures returns an empty list.  Malformed XML
    raises :class:`DGTFeedError`; it is never converted into an empty result.
    """

    try:
        root = ET.fromstring(xml_data)
    except (ET.ParseError, TypeError, ValueError) as error:
        raise DGTFeedError(f"El XML de la DGT no es válido: {error}") from error

    element_names = {_local_name(element.tag) for element in root.iter()}
    if not element_names.intersection({"payload", "payloadPublication", "situation"}):
        raise DGTFeedError(
            "El XML recibido no tiene la estructura de una publicación DATEX II"
        )

    closures: list[dict[str, Any]] = []
    for situation in (
        element for element in root.iter() if _local_name(element.tag) == "situation"
    ):
        for record in (
            element
            for element in situation.iter()
            if element is not situation and _local_name(element.tag) == "situationRecord"
        ):
            closure = _record_to_closure(situation, record)
            if closure is not None:
                closures.append(closure)
    return _group_opposite_directions(closures)


def parse_dgt_xml(xml_data: bytes | str) -> list[dict[str, Any]]:
    """Explicit alias for callers that prefer a source-specific parser name."""

    return parse_closures(xml_data)


def fetch_closures(
    url: str = DGT_FEED_URL,
    timeout: float = 60,
) -> list[dict[str, Any]]:
    """Download the official DGT feed and parse complete Andalusian closures.

    Network, HTTP, empty-body and XML errors are explicit ``DGTFeedError``
    failures.  This is important for the monitor: a failed download must never
    look like every road reopened at once.
    """

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1",
            "User-Agent": (
                "Mozilla/5.0 (compatible; "
                "CarreterasCortadasAndaluciaBot/1.0)"
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None and hasattr(response, "getcode"):
                status = response.getcode()
            if status is not None and status >= 400:
                raise DGTFeedError(f"La DGT respondió con HTTP {status}")
            payload = response.read()
    except DGTFeedError:
        raise
    except urllib.error.HTTPError as error:
        raise DGTFeedError(f"La DGT respondió con HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DGTFeedError(f"No se pudo descargar el feed de la DGT: {error}") from error

    if not payload or not payload.strip():
        raise DGTFeedError("La DGT devolvió una respuesta vacía")
    return parse_closures(payload)


__all__ = [
    "DGT_FEED_URL",
    "DGTFeedError",
    "DETAIL_REASON_MAP",
    "DIRECTION_MAP",
    "GENERIC_REASON_MAP",
    "fetch_closures",
    "parse_closures",
    "parse_dgt_xml",
]

