# -*- coding: utf-8 -*-
"""
서버 InfluxDB → 1분 평균 wide CSV (replay 입력).

서버(네이버 클라우드, backend 브랜치)의 nginx 가 InfluxDB 조회 전용 창구를 연다:
    https://smartcubens.duckdns.org/influx     (/api/v2/query 등 조회 엔드포인트만, 그 밖은 404)
토큰은 읽기 전용 토큰을 환경변수로(서버 .env 의 INFLUX_READ_TOKEN 또는 공개 읽기 토큰):
    read -rs INFLUX_READ_TOKEN && export INFLUX_READ_TOKEN      (저장소·명령 기록에 남기지 말 것)
창구가 막혀 있으면 서버에 SSH 터널(ssh -N -L 8086:127.0.0.1:8086 <서버>) 후 --url http://127.0.0.1:8086.

필요 패키지: influxdb-client (requirements-train.txt)
"""
import csv
import os
from datetime import datetime, timedelta, timezone

FLUX = '''
from(bucket: "{bucket}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "mqtt" and r.kind == "data")
  |> filter(fn: (r) => contains(value: r.cube, set: [{cubes}]))
  |> filter(fn: (r) => contains(value: r._field, set: [{fields}]))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "cube", "_field", "_value"])
'''


def columns_of(zone):
    """zone 매핑에서 필요한 'CUBE.field' 열 목록."""
    cols = []
    def add(m):
        if m is None:
            return
        if isinstance(m, list):
            for x in m:
                add(x)
        elif isinstance(m, dict):
            add(m.get("from"))
            add(m.get("valid_if"))
        else:
            cols.append(str(m))
    for m in zone.get("sensors", {}).values():
        add(m)
    for m in zone.get("actuators", {}).values():
        add(m)
    return sorted(set(cols))


def export(zone, start, stop, out_path, url, org="smartcube", bucket="sensors", token=None, chunk_hours=24):
    from influxdb_client import InfluxDBClient          # 지연 import — 핵심 모듈은 의존성 없이 시험되게

    token = token or os.environ.get("INFLUX_READ_TOKEN", "")
    if not token:
        raise SystemExit("INFLUX_READ_TOKEN 환경변수가 없다 (젯슨 /data/.env 의 읽기 전용 토큰)")
    cols = columns_of(zone)
    cubes = sorted({c.split(".", 1)[0] for c in cols})
    fields = sorted({c.split(".", 1)[1] for c in cols})
    rows = {}
    with InfluxDBClient(url=url, token=token, org=org, timeout=120_000) as client:
        q = client.query_api()
        t0 = start
        while t0 < stop:
            t1 = min(stop, t0 + timedelta(hours=chunk_hours))
            flux = FLUX.format(bucket=bucket, start=t0.isoformat(), stop=t1.isoformat(),
                               cubes=", ".join('"%s"' % c for c in cubes),
                               fields=", ".join('"%s"' % f for f in fields))
            for table in q.query(flux):
                for rec in table.records:
                    key = "%s.%s" % (rec.values["cube"], rec.get_field())
                    if key not in cols:
                        continue
                    ts = rec.get_time().astimezone(timezone.utc).replace(second=0, microsecond=0)
                    rows.setdefault(ts, {})[key] = rec.get_value()
            print("  %s ~ %s : 누적 %d 분" % (t0.isoformat(), t1.isoformat(), len(rows)))
            t0 = t1
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time"] + cols)
        for ts in sorted(rows):
            r = rows[ts]
            w.writerow([ts.isoformat()] + ["" if r.get(c) is None else "%.4f" % r[c] for c in cols])
    return len(rows)


def parse_time(s):
    """'2026-11-01' 또는 ISO 시각. 날짜만 주면 KST 0시."""
    if len(s) == 10:
        return datetime.fromisoformat(s).replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc)
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc)
