# Publicación gratuita del dashboard

## Propósito

El dashboard público permite recorrer el mapa, los indicadores por subzona, la evolución temporal y el detalle de las 40 plazas sin mantener la cuenta temporal de AWS Academy activa. La interfaz declara de forma visible que los estados son simulados.

## Modos de ejecución

- `SMART_PARKING_MODE=auto`: usa `API_BASE_URL` o `infra/infra_state.json` si contienen una API; si no, activa la simulación.
- `SMART_PARKING_MODE=demo`: fuerza datos deterministas generados localmente.
- `SMART_PARKING_MODE=live`: exige una API REST disponible y muestra el error de conexión si falla.

La simulación vive en `prototipo/dashboard/demo_data.py`. Parte de `infra/parking_zone_seed.json`, actualiza estados por minuto y genera 48 puntos temporales por subzona sin escribir en disco.

## Streamlit Community Cloud

- Repositorio: `alonsomarcosm99/smart-parking-albacete`.
- Rama: `main`.
- Fichero principal: `prototipo/dashboard/streamlit_app.py`.
- Dependencias: `prototipo/dashboard/requirements.txt`.
- Secretos: ninguno.

## Limitaciones

La publicación no demuestra una conexión AWS viva. La infraestructura real, su latencia y sus evidencias permanecen documentadas y reproducibles mediante los scripts del repositorio.

Última verificación local: 2026-06-22.
