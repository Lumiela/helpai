"""ESP32-CAM 2대로 스테레오 비전 거리 측정

사용법:
    python stereo.py test       # 두 카메라 연결 확인
    python stereo.py collect    # 캘리브레이션용 체커보드 사진 수집
    python stereo.py calibrate  # 스테레오 캘리브레이션 -> stereo_calib.npz 생성
    python stereo.py measure    # 실시간 거리 측정
"""
import glob
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import requests

# ===== 설정 =====
LEFT_URL = "http://192.168.0.101/capture"
RIGHT_URL = "http://192.168.0.102/capture"
BOARD = (9, 6)        # 체커보드 '내부 코너' 개수 (가로, 세로) - 10x7칸 보드면 (9, 6)
SQUARE_MM = 25.0      # 체커보드 한 칸 실제 크기(mm) - 자로 정확히 재서 입력
CALIB_DIR = "calib"
CALIB_FILE = "stereo_calib.npz"
NUM_DISP = 128        # 시차 탐색 범위(16의 배수). 가까운 물체가 측정 안 되면 늘리기
BLOCK = 5             # SGBM 블록 크기(홀수)
ROI = 7               # 거리 계산 시 주변 ROI x ROI 픽셀의 중앙값 사용
MAX_DIST_MM = 5000    # 이보다 먼 값은 신뢰하지 않음

_pool = ThreadPoolExecutor(2)
_sess_l, _sess_r = requests.Session(), requests.Session()


def _fetch(sess, url):
    data = sess.get(url, timeout=3).content
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"이미지 디코딩 실패: {url}")
    return img


def grab_pair():
    """두 카메라에 동시에 요청해서 촬영 시점 차이를 최소화"""
    fl = _pool.submit(_fetch, _sess_l, LEFT_URL)
    fr = _pool.submit(_fetch, _sess_r, RIGHT_URL)
    return fl.result(), fr.result()


# ---------- 0. 연결 테스트 ----------
def test():
    print("q: 종료")
    while True:
        t = time.time()
        L, R = grab_pair()
        fps = 1.0 / (time.time() - t)
        view = np.hstack([L, R])
        cv2.putText(view, f"{L.shape[1]}x{L.shape[0]}  {fps:.1f} FPS", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow("test (left | right)", view)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()


# ---------- 1. 캘리브레이션 사진 수집 ----------
def collect():
    os.makedirs(CALIB_DIR, exist_ok=True)
    n = len(glob.glob(f"{CALIB_DIR}/L_*.png"))
    print("SPACE: 저장 (양쪽 모두 인식될 때만) / q: 종료")
    print("체커보드를 거리·각도·화면 위치를 바꿔가며 20~30장 찍으세요.")
    while True:
        L, R = grab_pair()
        gl = cv2.cvtColor(L, cv2.COLOR_BGR2GRAY)
        gr = cv2.cvtColor(R, cv2.COLOR_BGR2GRAY)
        okl, cl = cv2.findChessboardCorners(gl, BOARD, flags=cv2.CALIB_CB_FAST_CHECK)
        okr, cr = cv2.findChessboardCorners(gr, BOARD, flags=cv2.CALIB_CB_FAST_CHECK)
        vl, vr = L.copy(), R.copy()
        cv2.drawChessboardCorners(vl, BOARD, cl, okl)
        cv2.drawChessboardCorners(vr, BOARD, cr, okr)
        view = np.hstack([vl, vr])
        color = (0, 255, 0) if okl and okr else (0, 0, 255)
        cv2.putText(view, f"L:{okl} R:{okr}  saved:{n}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.imshow("collect", view)
        k = cv2.waitKey(1) & 0xFF
        if k == ord(" ") and okl and okr:
            cv2.imwrite(f"{CALIB_DIR}/L_{n:02d}.png", L)
            cv2.imwrite(f"{CALIB_DIR}/R_{n:02d}.png", R)
            print(f"저장: {n:02d}")
            n += 1
        elif k == ord("q"):
            break
    cv2.destroyAllWindows()


# ---------- 2. 스테레오 캘리브레이션 ----------
def calibrate():
    objp = np.zeros((BOARD[0] * BOARD[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:BOARD[0], 0:BOARD[1]].T.reshape(-1, 2) * SQUARE_MM
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)

    obj, ptsL, ptsR, size = [], [], [], None
    for fl in sorted(glob.glob(f"{CALIB_DIR}/L_*.png")):
        fr = fl.replace("L_", "R_")
        gl, gr = cv2.imread(fl, 0), cv2.imread(fr, 0)
        if gl is None or gr is None:
            continue
        okl, cl = cv2.findChessboardCorners(gl, BOARD)
        okr, cr = cv2.findChessboardCorners(gr, BOARD)
        if okl and okr:
            obj.append(objp)
            ptsL.append(cv2.cornerSubPix(gl, cl, (11, 11), (-1, -1), crit))
            ptsR.append(cv2.cornerSubPix(gr, cr, (11, 11), (-1, -1), crit))
            size = gl.shape[::-1]
    if len(obj) < 10:
        sys.exit(f"사용 가능한 사진 쌍이 {len(obj)}개뿐입니다. 최소 10쌍(권장 20쌍 이상) 필요.")

    errL, K1, D1, _, _ = cv2.calibrateCamera(obj, ptsL, size, None, None)
    errR, K2, D2, _, _ = cv2.calibrateCamera(obj, ptsR, size, None, None)
    rms, K1, D1, K2, D2, R, T, _, _ = cv2.stereoCalibrate(
        obj, ptsL, ptsR, K1, D1, K2, D2, size,
        criteria=crit, flags=cv2.CALIB_FIX_INTRINSIC)
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K1, D1, K2, D2, size, R, T, alpha=0)

    print(f"사용한 사진 쌍: {len(obj)}")
    print(f"단일 카메라 오차  L: {errL:.3f}px  R: {errR:.3f}px")
    print(f"스테레오 RMS 오차: {rms:.3f}px  (0.5 이하 우수, 1.0 이상이면 재촬영 권장)")
    print(f"측정된 카메라 간격(baseline): {np.linalg.norm(T):.1f} mm")
    print(f"초점거리: {P1[0, 0]:.1f}px")
    np.savez(CALIB_FILE, K1=K1, D1=D1, K2=K2, D2=D2, R1=R1, R2=R2,
             P1=P1, P2=P2, Q=Q, size=np.array(size))
    print(f"저장 완료: {CALIB_FILE}")


# ---------- 3. 거리 측정 ----------
def _distance_at(z_map, valid, x, y):
    """(x, y) 주변 ROI에서 유효한 깊이값의 중앙값(mm). 없으면 None"""
    h, w = z_map.shape
    r = ROI // 2
    y0, y1, x0, x1 = max(0, y - r), min(h, y + r + 1), max(0, x - r), min(w, x + r + 1)
    zs = z_map[y0:y1, x0:x1][valid[y0:y1, x0:x1]]
    if zs.size < (ROI * ROI) // 4:
        return None
    return float(np.median(zs))


def _draw_marker(img, x, y, dist, color):
    cv2.drawMarker(img, (x, y), color, cv2.MARKER_CROSS, 20, 2)
    text = f"{dist / 10:.1f} cm" if dist is not None else "N/A"
    cv2.putText(img, text, (x + 12, y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
    cv2.putText(img, text, (x + 12, y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)


def measure():
    if not os.path.exists(CALIB_FILE):
        sys.exit(f"{CALIB_FILE} 가 없습니다. 먼저 collect -> calibrate 를 실행하세요.")
    c = np.load(CALIB_FILE)
    size = tuple(int(v) for v in c["size"])
    Q = c["Q"]
    mLx, mLy = cv2.initUndistortRectifyMap(c["K1"], c["D1"], c["R1"], c["P1"], size, cv2.CV_16SC2)
    mRx, mRy = cv2.initUndistortRectifyMap(c["K2"], c["D2"], c["R2"], c["P2"], size, cv2.CV_16SC2)

    sgbm = cv2.StereoSGBM_create(
        minDisparity=0, numDisparities=NUM_DISP, blockSize=BLOCK,
        P1=8 * 3 * BLOCK ** 2, P2=32 * 3 * BLOCK ** 2,
        disp12MaxDiff=1, uniquenessRatio=10,
        speckleWindowSize=100, speckleRange=2,
        mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)

    w, h = size
    target = [w // 2, h // 2]   # 측정 지점 (기본: 화면 중앙)
    smooth = None               # 프레임 간 흔들림을 줄이기 위한 이동평균

    def on_mouse(event, x, y, *_):
        nonlocal smooth
        if event == cv2.EVENT_LBUTTONDOWN:
            target[0], target[1] = x % w, y   # 왼쪽/오른쪽 화면 어디를 클릭해도 동작
            smooth = None
        elif event == cv2.EVENT_RBUTTONDOWN:
            target[0], target[1] = w // 2, h // 2
            smooth = None

    win = "distance (left click: select point / right click: center / q: quit)"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)

    while True:
        t = time.time()
        try:
            L, R = grab_pair()
        except Exception as e:
            print("캡처 실패:", e)
            continue
        if (L.shape[1], L.shape[0]) != size:
            sys.exit(f"현재 해상도 {L.shape[1]}x{L.shape[0]} 가 캘리브레이션 해상도 {size} 와 다릅니다.")

        rl = cv2.remap(L, mLx, mLy, cv2.INTER_LINEAR)
        rr = cv2.remap(R, mRx, mRy, cv2.INTER_LINEAR)
        gl = cv2.cvtColor(rl, cv2.COLOR_BGR2GRAY)
        gr = cv2.cvtColor(rr, cv2.COLOR_BGR2GRAY)

        disp = sgbm.compute(gl, gr).astype(np.float32) / 16.0
        pts = cv2.reprojectImageTo3D(disp, Q)
        z = pts[:, :, 2]
        valid = (disp > 0) & np.isfinite(z) & (z > 0) & (z < MAX_DIST_MM)

        d = _distance_at(z, valid, target[0], target[1])
        if d is not None:
            smooth = d if smooth is None else 0.7 * smooth + 0.3 * d
        shown = smooth if d is not None else None

        disp_vis = cv2.applyColorMap(
            np.clip(disp * (255.0 / NUM_DISP), 0, 255).astype(np.uint8), cv2.COLORMAP_JET)
        disp_vis[~valid] = 0

        view_l, view_d = rl.copy(), disp_vis
        _draw_marker(view_l, target[0], target[1], shown, (0, 255, 0))
        _draw_marker(view_d, target[0], target[1], shown, (255, 255, 255))
        fps = 1.0 / max(time.time() - t, 1e-6)
        cv2.putText(view_l, f"{fps:.1f} FPS", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        cv2.imshow(win, np.hstack([view_l, view_d]))
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("s"):
            cv2.imwrite(f"capture_{int(time.time())}.png", np.hstack([view_l, view_d]))
            print("스크린샷 저장")
    cv2.destroyAllWindows()


if __name__ == "__main__":
    cmds = {"test": test, "collect": collect, "calibrate": calibrate, "measure": measure}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]]()
