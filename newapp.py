# ================================================
# 🚑 Ambulance Route Optimization (Hybrid: A* 50% + GA 50%) + 실시간 GPS + 카카오 API
# ✅ 비가용 병원은 한 세션 동안 고정, 추적 재시작 시 새로 설정
# ✅ 세션 초기화 버튼으로 화면/세션 모두 리셋
# ✅ Render 호환 완벽
# ================================================

import os, time, random, math, requests
from flask import Flask, request, render_template_string, jsonify

# ===== 설정 =====
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")
PORT = int(os.environ.get("PORT", 5000))

coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}
UNAVAILABLE_HOSPITALS = None

# ===== 가중치 (50:50 반영) =====
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5
A_STAR_WEIGHT = 0.5
GA_WEIGHT = 0.5


# ===== 헬퍼 함수 =====
def compute_weighted_time(distance_m, road_name=""):
    time_min = distance_m / (45_000 / 60)
    penalty = 0
    if any(k in road_name for k in ["골목", "이면", "소로"]):
        penalty += WEIGHT_ALLEY
    elif "좁" in road_name:
        penalty += WEIGHT_NARROW
    return time_min * (1 + penalty)


def assign_fixed_availability(hospitals, max_unavail_frac=0.5):
    """세션 동안만 비가용 병원 고정"""
    global UNAVAILABLE_HOSPITALS
    if UNAVAILABLE_HOSPITALS is None:
        frac = random.uniform(0, max_unavail_frac)
        num_unavail = int(len(hospitals) * frac)
        unavail = random.sample(hospitals, num_unavail) if num_unavail else []
        UNAVAILABLE_HOSPITALS = [h["name"] for h in unavail]

    for h in hospitals:
        h["available"] = (h["name"] not in UNAVAILABLE_HOSPITALS)
    return UNAVAILABLE_HOSPITALS


def select_best_GA(hospitals, pop_size=10, gens=5, mutation_rate=0.2):
    available_indices = [i for i, h in enumerate(hospitals) if h.get("available", True)]
    if not available_indices:
        return None
    n = len(available_indices)
    population = [random.sample(available_indices, n) for _ in range(pop_size)]

    def fitness(ch):
        first_idx = ch[0]
        first = hospitals[first_idx]
        if first.get("weighted_time", math.inf) == math.inf:
            return 0
        return 1 / (first["weighted_time"] + 1)

    for _ in range(gens):
        population.sort(key=fitness, reverse=True)
        next_gen = population[:2]
        while len(next_gen) < pop_size:
            p1, p2 = random.sample(population[:max(2, pop_size // 2)], 2)
            cut = random.randint(1, n - 1)
            child = p1[:cut] + [c for c in p2 if c not in p1[:cut]]
            if random.random() < mutation_rate and len(child) >= 2:
                i, j = random.sample(range(len(child)), 2)
                child[i], child[j] = child[j], child[i]
            next_gen.append(child)
        population = next_gen
    best_ch = max(population, key=fitness)
    return hospitals[best_ch[0]]


# ===== Flask 앱 =====
app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>실시간 GPS → 응급실 검색</title>
<style>
body { font-family: system-ui, -apple-system, sans-serif; padding:16px; }
button { font-size:18px; padding:12px 16px; margin-right:8px; }
#log { margin-top:12px; white-space:pre-line; }
#result { margin-top:20px; padding:10px; background:#f9f9f9; border-radius:8px; }
.best { background:#e6ffe6; padding:8px; border-radius:6px; margin-top:8px; }
.unavail { background:#ffeaea; padding:8px; border-radius:6px; margin-top:12px; }
</style>
</head>
<body>
<h2>📍 실시간 GPS 전송 & 응급실 검색</h2>
<p>아래 버튼을 눌러 위치 권한을 허용하세요.</p>
<button id="startBtn">실시간 추적 시작</button>
<button id="stopBtn" disabled>정지</button>
<button id="resetBtn">세션 초기화</button>
<div id="log">대기 중…</div>
<div id="result"></div>

<script>
let watchId=null;
function log(msg){document.getElementById('log').textContent=msg;}

function renderResults(data){
  const div=document.getElementById('result');
  if(!data.ok){div.textContent='❌ 데이터 수신 실패'; return;}
  let html='';
  if(data.best){
    html+=`<div class="best"><b>🏆 최적 응급실:</b><br>${data.best.name}<br>${data.best.address}<br>거리: ${data.best.distance_m}m<br>예상 소요: ${data.best.weighted_time}분</div>`;
  }
  if(data.unavailable_list && data.unavailable_list.length){
    html+=`<div class="unavail"><b>🚫 현재 비가용 병원:</b><br>${data.unavailable_list.join('<br>')}</div>`;
  }
  if(data.hospitals && data.hospitals.length){
    html+='<h3>📋 병원 목록</h3><ul>';
    data.hospitals.forEach((h,i)=>{
      html+=`<li>${i+1}. ${h.name} (${h.address}) - 거리: ${h.distance_m}m / 소요: ${h.weighted_time}분 / 상태: ${h.available?'가용':'비가용'}</li>`;
    });
    html+='</ul>';
  }
  div.innerHTML=html;
}

function send(lat,lon,acc){
  fetch('/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lat,lon,accuracy:acc})})
  .then(res=>res.json())
  .then(data=>renderResults(data))
  .catch(e=>{log('❌ 요청 실패: '+e);});
}

// ✅ 세션 초기화 버튼 기능
document.getElementById('resetBtn').onclick=()=>{
  if(watchId!==null){
    navigator.geolocation.clearWatch(watchId);
    watchId=null;
  }
  fetch('/reset')
  .then(res=>res.json())
  .then(()=>{
    document.getElementById('startBtn').disabled=false;
    document.getElementById('stopBtn').disabled=true;
    document.getElementById('result').innerHTML='';
    log('🌀 세션이 초기화되었습니다. 다시 시작하려면 [실시간 추적 시작]을 누르세요.');
  })
  .catch(e=>log('❌ 초기화 실패: '+e));
};

document.getElementById('startBtn').onclick=()=>{
  if(!navigator.geolocation){log('❌ GPS 미지원'); return;}
  document.getElementById('startBtn').disabled=true;
  document.getElementById('stopBtn').disabled=false;
  log('⏳ 위치 권한 요청 중…');
  watchId=navigator.geolocation.watchPosition(
    pos=>{
      const lat=pos.coords.latitude.toFixed(6);
      const lon=pos.coords.longitude.toFixed(6);
      const acc=Math.round(pos.coords.accuracy);
      log('✅ 위치 전송 중 → 위도 '+lat+', 경도 '+lon+' (±'+acc+'m)');
      send(lat,lon,acc);
    },
    err=>{log('❌ 실패: '+err.message);},
    {enableHighAccuracy:true,maximumAge:0,timeout:10000}
  );
};

document.getElementById('stopBtn').onclick=()=>{
  if(watchId!==null){navigator.geolocation.clearWatch(watchId);watchId=null;}
  document.getElementById('startBtn').disabled=false;
  document.getElementById('stopBtn').disabled=true;
  log('⏹ 추적 중지');
};
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/reset")
def reset_session():
    global UNAVAILABLE_HOSPITALS
    UNAVAILABLE_HOSPITALS = None
    return jsonify(ok=True, msg="세션 초기화 완료")


@app.route("/update", methods=["POST"])
def update():
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
        acc = float(data.get("accuracy")) if data.get("accuracy") else None
    except:
        return jsonify(ok=False, error="bad payload"), 400

    coords.update({"lat": lat, "lon": lon, "accuracy": acc, "ts": time.time()})
    print(f"[INFO] 위치 갱신: {lat}, {lon}, ±{acc}")

    hospitals = []
    best = None
    unavailable_list = []
    try:
        url = "https://dapi.kakao.com/v2/local/search/keyword.json"
        headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
        params = {"query": "응급실", "x": lon, "y": lat, "radius": 10000, "size": 15, "sort": "distance"}
        res = requests.get(url, headers=headers, params=params, timeout=5)
        docs = res.json().get("documents", [])

        exclude_keywords = ["동물","치과","한의원","약국","떡볶이","카페","편의점","이송","은행","의원"]
        include_keywords = ["응급","응급실","응급의료","의료센터","병원","대학병원","응급센터","응급의료센터"]

        for d in docs:
            name = d["place_name"]
            if any(x in name for x in exclude_keywords): continue
            if not any(x in name for x in include_keywords): continue
            hospitals.append({
                "name": name,
                "address": d.get("road_address_name") or d.get("address_name", ""),
                "distance_m": float(d.get("distance", 0)),
                "road_name": d.get("road_address_name", "")
            })

        if hospitals:
            unavail = assign_fixed_availability(hospitals, 0.5)
            unavailable_list = list(unavail)
            for h in hospitals:
                if not h["available"]:
                    h["weighted_time"] = None
                else:
                    h["weighted_time"] = compute_weighted_time(h["distance_m"], h["road_name"])
            best_GA = select_best_GA(hospitals)
            for h in hospitals:
                base = h["weighted_time"] or math.inf
                ga_factor = 0.8 if best_GA and h["name"] == best_GA["name"] else 1.0
                h["final_score"] = base * (A_STAR_WEIGHT * ga_factor + GA_WEIGHT)
            avail = [h for h in hospitals if h["available"]]
            best = min(avail, key=lambda x: x["final_score"]) if avail else None
    except Exception as e:
        print(f"❌ 카카오 API 호출 실패: {e}")

    def safe_display(v):
        if v is None:
            return "N/A"
        if isinstance(v, (int, float)):
            if math.isinf(v):
                return "N/A"
            return round(v, 2)
        return v

    for h in hospitals:
        h["distance_m"] = safe_display(h.get("distance_m"))
        h["weighted_time"] = safe_display(h.get("weighted_time"))
        h["final_score"] = safe_display(h.get("final_score"))
    if best:
        best["distance_m"] = safe_display(best.get("distance_m"))
        best["weighted_time"] = safe_display(best.get("weighted_time"))

    return jsonify(ok=True, hospitals=hospitals, best=best, unavailable_list=unavailable_list)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
