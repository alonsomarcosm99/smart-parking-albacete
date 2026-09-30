# Prototipo Smart Parking Albacete

Este proyecto plantea una solución IoT de **aparcamiento inteligente** para una zona piloto del entorno universitario de Albacete. El caso de uso parte de una licitación ficticia del Ayuntamiento de Albacete y de una respuesta técnica de TECO S.L. El objetivo no es solo contar cuántas plazas están libres, sino diseñar una arquitectura completa capaz de recoger datos desde sensores, procesarlos, mantener el estado actualizado de cada plaza y exponer esa información a aplicaciones externas, paneles urbanos o futuros sistemas de movilidad conectada.

Prototipo funcional minimo del proyecto. Demuestra el flujo extremo a extremo:

```
Simulador (paho-mqtt, mTLS) -> AWS IoT Core -> IoT Rule -> Lambda ingesta
       -> DynamoDB (estado)
       -> Lambda agregador -> DynamoDB (KPIs)
       -> API Gateway REST -> Streamlit dashboard / terceros
```

![Arquitectura cloud del prototipo](../memoria/imagenes/diagrama_arquitectura.png)

## Requisitos

- Python 3.11 o superior (probado en 3.13).
- AWS Academy Learner Lab activo con credenciales en `~/.aws/credentials`.
- Region `us-east-1` (modificable via variable `AWS_REGION`).

## Instalacion

```powershell
python -m pip install -r requirements.txt
copy .env.example .env
```

## Despliegue (en orden)

```powershell
python infra/01_setup_iot_core.py
python infra/02_setup_dynamodb.py
python infra/03_setup_lambda.py
python infra/04_setup_api_gateway.py
```

Tras `01_*` se generan los certificados X.509 dentro de `simulator/certs/`.
Tras `04_*` la URL base de la API queda persistida en `infra/infra_state.json`.

![Things creados en AWS IoT Core](../memoria/imagenes/captura_aws_iot_things.png)

La captura anterior muestra el inventario de Things generado por el despliegue. Cada plaza simulada queda registrada con un identificador `ALB-Zx-NNN`.

![Regla MQTT de AWS IoT Core](../memoria/imagenes/captura_aws_iot_topic_rule.png)

La regla `smart_parking_albacete_ingest_rule` enruta los mensajes del topic `parking/+/spot/+/status` hacia la Lambda de ingesta.

![Funciones Lambda del prototipo](../memoria/imagenes/captura_lambda_functions.png)

Las tres Lambdas desplegadas separan responsabilidades: ingesta de eventos, agregacion de KPIs y lectura para la API.

### Cambio de cuenta AWS Academy

Si se recrea el proyecto en una cuenta nueva de Learner Lab, no se debe
reutilizar el certificado X.509 de una cuenta anterior. El sintoma tipico es:

- `python infra\01_setup_iot_core.py` muestra `Certificado reutilizado`.
- `infra\infra_state.json` contiene un `certificateArn` con un Account ID viejo.
- La API responde, pero `Invoke-RestMethod "$base/spots"` sigue devolviendo
  `count = 0` despues de ejecutar el simulador.

En ese caso, fuerza la creacion de un certificado nuevo para la cuenta activa:

```powershell
$stamp = Get-Date -Format yyyyMMdd_HHmmss
if (Test-Path simulator\certs) {
  Rename-Item simulator\certs "certs_old_$stamp"
}

$state = Get-Content infra\infra_state.json | ConvertFrom-Json
$state.PSObject.Properties.Remove("certificateId")
$state.PSObject.Properties.Remove("certificateArn")
$json = $state | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText(
  (Resolve-Path infra\infra_state.json),
  $json,
  [System.Text.UTF8Encoding]::new($false)
)

python infra\01_setup_iot_core.py
```

Comprueba despues que `certificateArn` apunta al Account ID actual:

```powershell
(Get-Content infra\infra_state.json | ConvertFrom-Json).certificateArn
aws sts get-caller-identity
```

Solo entonces vuelve a ejecutar el simulador.

## Simulador

```powershell
python simulator/fleet_runner.py --num-spots 40 --duration 300 --heartbeat 30 --tick 2
```

Cada sensor publica un evento por cambio de estado y un heartbeat cada 30 s.
El ultimo sensor de la flota se simula como caido para ilustrar deteccion de
averias en la memoria.

Para una demo rapida y para sembrar DynamoDB antes de capturar el dashboard, es
suficiente ejecutar 30 segundos:

```powershell
python simulator\fleet_runner.py --num-spots 40 --duration 30 --heartbeat 20 --tick 2
```

Resultado validado en la cuenta nueva del Learner Lab:

```powershell
$base = (Get-Content infra\infra_state.json | ConvertFrom-Json).apiBaseUrl
$base
# https://brxxgikhi6.execute-api.us-east-1.amazonaws.com/prod

Invoke-RestMethod "$base/spots" | Select-Object count
# count = 0 antes del simulador

Invoke-RestMethod "$base/zones"
# count = 0 antes del simulador

python simulator\fleet_runner.py --num-spots 40 --duration 30 --heartbeat 20 --tick 2

Invoke-RestMethod "$base/spots" | Select-Object count
# count = 40

Invoke-RestMethod "$base/zones"
# count = 4
```

![Tabla DynamoDB de estado actual](../memoria/imagenes/captura_dynamodb_state_items.png)

Tras ejecutar el simulador, la tabla `smart-parking-albacete-state` queda sembrada con una fila por plaza reportada.

![Tabla DynamoDB de KPIs por zona](../memoria/imagenes/captura_dynamodb_zone_kpis.png)

La tabla `smart-parking-albacete-zone-kpis` guarda los agregados temporales calculados por zona.

## Dashboard

```powershell
streamlit run dashboard/streamlit_app.py
```

Se abre en `http://localhost:8501`. Toma la URL de la API de
`infra/infra_state.json` automaticamente.

![Stage prod de API Gateway](../memoria/imagenes/captura_api_gateway_stage.png)

API Gateway publica el stage `prod`, que es la URL consumida por el dashboard y por clientes externos.

![Dashboard Streamlit del prototipo](../memoria/imagenes/captura_dashboard_streamlit.png)

## Verificacion

![Metricas de CloudWatch de la Lambda API](../memoria/imagenes/captura_cloudwatch_lambda_logs.png)

CloudWatch permite comprobar invocaciones, duracion, errores y concurrencia de la Lambda `smart-parking-albacete-api` durante la prueba.

## Teardown

```powershell
python infra/99_teardown.py
```

Borra IoT Things, certificado, policy, Topic Rule, Lambdas, tablas DynamoDB y
API Gateway. Hay que ejecutarlo antes de que caduquen las credenciales del lab.
