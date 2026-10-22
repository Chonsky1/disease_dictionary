# 지도 경계 데이터

- `world_110m.json`: [sane-topojson](https://github.com/etpinard/sane-topojson) 4.0.0 (MIT), 원자료 [Natural Earth](https://www.naturalearthdata.com/) 1:110m (public domain)
- plotly 지도는 기본적으로 cdn.plot.ly에서 이 파일을 받는데, 학교·사내망에서 막히면 지도가 빈 화면이 된다.
  그래서 앱과 함께 두고 Streamlit 정적 파일(`/app/static/…`)로 제공한다 (`.streamlit/config.toml`의 `enableStaticServing`).
