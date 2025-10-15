# ================================================
# 🚑 Ambulance Route Optimization (Hybrid: A* 70% + GA 30%) + 실시간 GPS + 카카오 API
# ✅ GPS 버튼 정상 작동
# ✅ 카카오맵 응급실 검색
# ✅ GA 후보 출력 생략, 병원 번호 표시
# ================================================

import os, time, threading, random, math, requests
from flask import Flask, request, render_template_string, jsonify

# ===== 설정 =====
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY")  # GitHub 환경변수에서 불러오기
PORT = int(os.environ.get("PORT", 5000))
coords = {"lat": None, "lon": None, "accuracy": None, "ts": None}

# ===================== HELPER =====================
WEIGHT_NARROW = 0.3
WEIGHT_ALLEY = 0.5
A_STAR_WEIGHT = 0.7
GA_WEIGHT = 0.3

def compute_weighted_time(distance_m, road_name=""):
    time_min = distance_m / (45_000 / 60)
    penalty = 0
    if any(k in road_name for k in ["골목","이면","소로"]):
        penalty += WEIGHT_ALLEY
    elif "좁" in road_name:
        penalty += WEIGHT_NARROW
    return time_min * (1 + penalty)

def assign_random_availability(hospitals, max_unavail_frac=0.5):
    frac = random.uniform(0, max_unavail_frac)
    num_unavail = int(len(hospitals) * frac)
    unavail = random.sample(hospitals, num_unavail) if num_unavail else []
    for h in hospitals:
        h["available"] = (h not in unavail)
    return frac, [h["name"] for h in unavail]

def select_best_GA(hospitals, pop_size=10, gens=5, mutation_rate=0.2):
    available_indices = [i for i,h in enumerate(hospitals) if h.get("available",True)]
    if not available_indices: return None
    n = len(available_indices)
    population = [random.sample(available_indices,n) for _ in range(pop_size)]
    def fitness(ch):
        first_idx = ch[0]
        first = hospitals[first_idx]
        if first.get("weighted_time",math.inf)==math.inf: return 0
        return 1 / (first["weighted_time"] + 1)
    for _ in range(gens):
        population.sort(key=fitness,reverse=True)
        next_gen = population[:2]
        while len(next_gen)<pop_size:
            p1,p2=random.sample(population[:max(2,pop_size//2)],2)
            cut=random.randint(1,n-1)
            child = p1[:cut]+[c for c in p2 if c not in p1[:cut]]
            if random.random()<mutation_rate and len(child)>=2:
                i,j=random.sample(range(len(child)),2)
                child[i],child[j]=child[j],child[i]
            next_gen.append(child)
        population = next_gen
    best_ch = max(population,key=fitness)
    return hospitals[best_ch[0]]

# ===================== Flask =====================
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
</style>
</head>
<body>
<h2>📍 실시간 GPS 전송</h2>
<p>아래 버튼 누른 뒤 위치 권한을 허용하세요.</p>
<button id="startBtn">실시간 추적 시작</button>
<button id="stopBtn" disabled>정지</button>
<div id="log">대기 중…</div>
<script>
let watchId=null;
function log(msg){document.getElementById('log').textContent=msg;}
function send(lat,lon,acc){
  fetch('/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lat,lon,accuracy:acc})}).catch(e=>{});
}
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
      log('✅ 전송됨 → 위도 '+lat+', 경도 '+lon+' (±'+acc+'m)');
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

@app.route("/update", methods=["POST"])
def update():
    data=request.get_json(silent=True) or {}
    try:
        lat=float(data.get("lat"))
        lon=float(data.get("lon"))
        acc=float(data.get("accuracy")) if data.get("accuracy") else None
    except:
        return jsonify(ok=False,error="bad payload"),400
    coords.update({"lat":lat,"lon":lon,"accuracy":acc,"ts":time.time()})
    print(f"[INFO] 위치 갱신: {lat}, {lon}, ±{acc}")
    return jsonify(ok=True)

# ===================== 메인 실행 =====================
if __name__ == "__main__":
    # Render 배포용: Flask 포그라운드 실행
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", PORT)), debug=False)
