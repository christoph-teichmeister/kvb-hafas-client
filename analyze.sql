-- Taktungsanalyse auf dem erfassten Soll-Fahrplan (fetch_timetable.py).
-- Aufruf z.B.:  sqlite3 -box timetable.db < analyze.sql
--
-- Alle Zeiten sind Soll-Zeiten. Verspätungen kommen hier nicht vor — die gibt
-- die API rückblickend nicht her (siehe docs/API.md, "Historische Daten").
--
-- Zeitdifferenzen laufen über strftime('%s') (ganze Sekunden), nicht über
-- julianday(): julianday liefert Fließkomma, eine Minute ergibt dort
-- 0.99999994, und CAST(... AS INTEGER) schneidet das auf 0 ab. Jede
-- Minutendifferenz wäre dadurch um bis zu eine Minute zu klein.
--
-- Hinweis zu Haltestellen-IDs: stop.ext_id ist eine Mast-ID, also pro
-- Richtung/Bahnsteig verschieden. stop.station_id ist die zugehörige
-- Master-Haltestelle — danach wird gruppiert, wenn "die Haltestelle"
-- unabhängig von der Richtung gemeint ist. Über den Namen zu gruppieren wäre
-- anfällig: zwei Haltestellen dürfen gleich heißen, und Mast-Namen tragen
-- teils Zusätze.

.mode box
.headers on

-- ---------------------------------------------------------------------------
-- 0. Überblick: was liegt überhaupt in der DB?
-- ---------------------------------------------------------------------------
SELECT '— Bestand —' AS "";
SELECT j.line,
       j.service_date,
       COUNT(*)                      AS fahrten,
       COUNT(DISTINCT j.direction)   AS richtungen,
       MIN(j.stop_count)             AS min_halte,
       MAX(j.stop_count)             AS max_halte
FROM journey j
GROUP BY j.line, j.service_date;

-- Kurzläufer/Verstärker: Fahrten, die nicht den vollen Laufweg fahren.
-- Die müssen bei der Taktbewertung gesondert betrachtet werden, sonst sehen
-- die Randabschnitte künstlich schlechter versorgt aus.
SELECT '— Laufweglängen (Kurzläufer erkennen) —' AS "";
SELECT direction, stop_count, COUNT(*) AS fahrten, MIN(service_days) AS verkehrstage
FROM journey
GROUP BY direction, stop_count
ORDER BY direction, stop_count;

-- ---------------------------------------------------------------------------
-- 1. Taktabstände (Headways) je Haltestelle und Richtung
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS headway;
CREATE TEMP VIEW headway AS
WITH dep AS (
    SELECT s.name                AS haltestelle,
           s.station_id          AS station_id,
           j.direction           AS richtung,
           st.dep_planned        AS abfahrt,
           CAST(strftime('%H', st.dep_planned) AS INTEGER) AS stunde,
           LAG(st.dep_planned) OVER (
               PARTITION BY st.stop_ext_id, j.direction
               ORDER BY st.dep_planned
           ) AS vorherige
    FROM stop_time st
    JOIN journey j ON j.jid = st.jid
    JOIN stop    s ON s.ext_id = st.stop_ext_id
    WHERE st.dep_planned IS NOT NULL
)
SELECT haltestelle, station_id, richtung, abfahrt, stunde,
       (CAST(strftime('%s', abfahrt) AS INTEGER)
        - CAST(strftime('%s', vorherige) AS INTEGER)) / 60 AS takt_min
FROM dep
WHERE vorherige IS NOT NULL;

SELECT '— Takt je Stunde und Richtung (netzweit über alle Halte) —' AS "";
SELECT richtung,
       stunde,
       COUNT(*)                     AS abfahrten,
       MIN(takt_min)                AS min_takt,
       ROUND(AVG(takt_min), 1)      AS schnitt,
       MAX(takt_min)                AS max_takt
FROM headway
GROUP BY richtung, stunde
ORDER BY richtung, stunde;

-- ---------------------------------------------------------------------------
-- 2. Taktbrüche — der eigentliche Ertrag
-- ---------------------------------------------------------------------------
-- Wo weicht ein einzelner Abstand stark vom typischen Abstand derselben
-- Haltestelle/Richtung/Stunde ab? Das sind die konkreten Kandidaten für
-- "diese Fahrt zwei Minuten verschieben".
-- Die nächtliche Betriebspause ist der mit Abstand größte "Taktbruch" des
-- Tages, aber keiner: sie ist gewollt. Ohne die Grenze unten dominiert sie
-- die Liste und verdeckt die echten Unregelmäßigkeiten im Tagesverkehr.
SELECT '— Betriebspause (längste Lücke je Richtung) —' AS "";
SELECT richtung, haltestelle, abfahrt AS wiederaufnahme, takt_min AS pause_min
FROM headway h
WHERE takt_min = (SELECT MAX(takt_min) FROM headway x WHERE x.richtung = h.richtung)
GROUP BY richtung;

SELECT '— Größte Taktbrüche im Tagesverkehr (Lücke > 1,8x Stundenschnitt) —' AS "";
WITH tagsueber AS (
    -- Lücken ab einer Stunde sind Betriebspause oder Randzeit, nicht Taktbruch.
    SELECT * FROM headway WHERE takt_min < 60
),
norm AS (
    SELECT t.*,
           AVG(takt_min) OVER (PARTITION BY station_id, richtung, stunde) AS schnitt
    FROM tagsueber t
)
SELECT haltestelle, richtung, abfahrt, takt_min, ROUND(schnitt, 1) AS stundenschnitt
FROM norm
WHERE schnitt > 0 AND takt_min > schnitt * 1.8
ORDER BY takt_min DESC
LIMIT 25;

-- Das Gegenstück: Pulks. Zwei Fahrten dicht hintereinander, danach eine Lücke —
-- typisch für schlecht verzahnte Linienäste.
SELECT '— Pulkbildung (Abstand <= 2 min) —' AS "";
SELECT haltestelle, richtung, abfahrt, takt_min
FROM headway
WHERE takt_min <= 2
ORDER BY abfahrt
LIMIT 25;

-- ---------------------------------------------------------------------------
-- 3. Randzeiten: Betriebsbeginn, Betriebsende, Nachtlücke
-- ---------------------------------------------------------------------------
SELECT '— Erste/letzte Abfahrt je Haltestelle und Richtung —' AS "";
SELECT MIN(s.name) AS haltestelle,
       s.station_id,
       j.direction AS richtung,
       MIN(st.dep_planned) AS erste,
       MAX(st.dep_planned) AS letzte,
       COUNT(*) AS abfahrten
FROM stop_time st
JOIN journey j ON j.jid = st.jid
JOIN stop    s ON s.ext_id = st.stop_ext_id
WHERE st.dep_planned IS NOT NULL
GROUP BY s.station_id, j.direction
ORDER BY haltestelle, j.direction;

-- ---------------------------------------------------------------------------
-- 4. Fahrzeit über den Tag — zeigt, wo schon Puffer im Fahrplan steckt
-- ---------------------------------------------------------------------------
SELECT '— Fahrzeit Start->Ziel nach Abfahrtsstunde —' AS "";
WITH laufzeit AS (
    SELECT j.jid,
           j.direction,
           j.stop_count,
           MIN(st.dep_planned) AS start,
           MAX(COALESCE(st.arr_planned, st.dep_planned)) AS ziel
    FROM stop_time st
    JOIN journey j ON j.jid = st.jid
    GROUP BY j.jid
    HAVING start IS NOT NULL AND ziel IS NOT NULL
)
SELECT direction,
       stop_count,
       CAST(strftime('%H', start) AS INTEGER) AS abfahrtsstunde,
       COUNT(*) AS fahrten,
       ROUND(AVG((CAST(strftime('%s', ziel) AS INTEGER)
                  - CAST(strftime('%s', start) AS INTEGER)) / 60.0), 1) AS fahrzeit_min,
       MIN((CAST(strftime('%s', ziel) AS INTEGER)
            - CAST(strftime('%s', start) AS INTEGER)) / 60) AS min_min,
       MAX((CAST(strftime('%s', ziel) AS INTEGER)
            - CAST(strftime('%s', start) AS INTEGER)) / 60) AS max_min
FROM laufzeit
GROUP BY direction, stop_count, abfahrtsstunde
ORDER BY direction, stop_count, abfahrtsstunde;

-- ---------------------------------------------------------------------------
-- 5. Richtungssymmetrie — fährt die Gegenrichtung zur selben Zeit genauso dicht?
-- ---------------------------------------------------------------------------
SELECT '— Takt Hin- vs. Rückrichtung je Stunde —' AS "";
SELECT stunde,
       ROUND(AVG(CASE WHEN richtung = 'Heumarkt'         THEN takt_min END), 1) AS richtung_heumarkt,
       ROUND(AVG(CASE WHEN richtung = 'Am Butzweilerhof' THEN takt_min END), 1) AS richtung_butzweilerhof,
       ROUND(ABS(AVG(CASE WHEN richtung = 'Heumarkt'         THEN takt_min END)
               - AVG(CASE WHEN richtung = 'Am Butzweilerhof' THEN takt_min END)), 1) AS differenz
FROM headway
GROUP BY stunde
ORDER BY stunde;

-- ---------------------------------------------------------------------------
-- 6. Plausibilisierung — vor jeder inhaltlichen Aussage laufen lassen
-- ---------------------------------------------------------------------------
SELECT '— Plausibilisierung (alles hier sollte 0 Zeilen liefern) —' AS "";

-- Zeiten innerhalb einer Fahrt müssen monoton steigen. Schlägt das an, ist die
-- Mitternachts-Behandlung in storage.parse_hafas_time kaputt.
SELECT 'nicht monoton' AS problem, jid, idx, dep_planned, vorherige
FROM (
    SELECT jid, idx, dep_planned,
           LAG(COALESCE(dep_planned, arr_planned)) OVER (PARTITION BY jid ORDER BY idx) AS vorherige
    FROM stop_time
)
WHERE dep_planned IS NOT NULL AND vorherige IS NOT NULL AND dep_planned < vorherige;

-- Fahrten ohne Laufweg (abgebrochener Lauf) — fetch_timetable.py erneut aufrufen.
SELECT 'ohne Laufweg' AS problem, j.jid
FROM journey j LEFT JOIN stop_time st ON st.jid = j.jid
WHERE st.jid IS NULL;
