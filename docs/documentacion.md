# FacturIA — Documentación del proyecto

### Qué es FacturIA

FacturIA es una aplicación web **multi-empresa** (multi-tenant) para gestionar facturas y movimientos financieros. Cada empresa cliente tiene sus propios usuarios, terceros (proveedores y clientes), documentos y movimientos, y nunca ve los datos de otra.

Su punto fuerte es la **lectura automática de documentos con IA**: el usuario sube una factura, ticket, presupuesto o albarán (PDF o foto) y la aplicación extrae los datos con la API de Claude. Después una persona revisa el resultado, lo aprueba o lo corrige, y puede exportarlo o consultarlo en métricas.

- Idioma del producto: español
- Usuarios: empresas y gestorías que necesitan registrar compras y ventas sin teclear cada factura
- Estado: en desarrollo, con el flujo antiguo de n8n todavía activo en paralelo

### Cómo funciona la app (resumen)

1. **Subida**: el usuario arrastra archivos o hace una foto desde el móvil. Se valida el tamaño y el tipo real del archivo, y se detectan duplicados (el mismo archivo no se sube dos veces).
2. **Cola de procesado**: cada documento entra en una cola (Celery + Redis) y se procesa en segundo plano. La interfaz se refresca sola mientras hay documentos en proceso.
3. **Lectura con IA**: se prepara el archivo (conversión de HEIC, orientación, reducción de tamaño) y se envía a Claude, que devuelve los datos estructurados: emisor, receptor, NIF, fechas, importes, desglose de IVA, retenciones y líneas.
4. **Validación automática**: reglas deterministas comprueban que todo cuadra (base + IVA + recargo − retención = total), que los NIF/CIF son válidos, que las fechas son razonables y que no es un duplicado. Cada problema se marca como error, aviso o información.
5. **Revisión humana**: el documento queda pendiente con un nivel de revisión recomendado. El usuario puede aprobarlo, rechazarlo, editar campos, archivarlo, rectificarlo o relanzar la lectura si falló.
6. **Organización y consulta**: los documentos se clasifican solos en la vista «Archivo» y se pueden filtrar, exportar y analizar en métricas.

### Funcionalidades principales

- **Panel de inicio** con resumen y gráficos del periodo
- **Listado de documentos** con filtros, pestañas por estado y paginación
- **Detalle de documento** con visor del PDF/imagen al lado de los datos extraídos y las incidencias detectadas
- **Rectificaciones**: se crean nuevas versiones de un documento manteniendo el historial; una rectificativa siempre resta en totales
- **Vista «Archivo»**: carpetas automáticas por Compras/Ventas → año → trimestre → empresa → tipo de documento, y una sección «Sin clasificar» para lo que falte por completar
- **Mis carpetas**: carpetas manuales anidadas que crea el usuario
- **Terceros**: proveedores y clientes sin duplicados (se unifican por NIF o por nombre normalizado)
- **Finanzas**: movimientos financieros con categorías y recibos adjuntos
- **Exportación** a CSV, Excel y PDF, con previsualización
- **Métricas** por periodo (ingresos, gastos, impuestos)
- **Auditoría**: cada acción sobre un documento queda registrada

### Lectura de documentos con IA

- Modelo principal: **Claude Haiku** (rápido y económico)
- Modelo de respaldo: **Claude Sonnet**, solo cuando hace falta (respuesta inválida, baja confianza o un dato que el modelo puede corregir, como un NIF mal leído o un descuadre)
- Cada intento queda registrado con tokens consumidos y coste estimado
- **Reintentos automáticos** ante errores temporales (límite de peticiones, caídas, timeouts) con espera creciente; los errores definitivos (archivo dañado, demasiadas páginas) marcan el documento como fallido para revisarlo a mano
- **Batch API**: opción de procesar lotes de forma diferida a mitad de precio
- El sentido de la factura (compra o venta) no lo decide la IA: se calcula comparando el NIF del documento con el de la empresa cliente
- **Auto-aprobación** desactivada por defecto; si se activa, solo aprueba documentos sin errores ni avisos y con confianza alta

### Servicios y componentes

La aplicación se divide en varias apps Django y servicios de apoyo:

- **clients**: empresas cliente, usuarios y ficha de empresa con logo
- **documents**: el núcleo; subida, procesado, revisión, organización, exportación y métricas
- **finance**: movimientos financieros y sus categorías
- **api**: API REST (ingesta desde n8n, listado de documentos y métricas) con autenticación por API key o sesión

Dentro de `documents/services/` vive la lógica de negocio, separada de las vistas:

- **upload / processing**: recepción de archivos y orquestación del procesado
- **extraction**: integración con Claude (prompts, esquemas, normalización, costes, Batch API)
- **validation / taxes / duplicates**: reglas de cuadre, impuestos y detección de duplicados
- **companies**: alta y unificación de terceros
- **organization / folders**: tipo, carpeta, etiquetas y carpetas manuales
- **ingest**: entrada de datos desde n8n
- **usage**: agregado de tokens y costes
- **retention**: conservación y borrado de documentos caducados (RGPD)

Servicios de infraestructura:

- **PostgreSQL**: base de datos principal
- **Redis**: broker de la cola de tareas
- **Celery worker**: procesa los documentos en segundo plano
- **Cloudflare R2** (UE): almacenamiento privado de archivos; nunca tienen URL pública, se sirven a través de vistas autenticadas
- **API de Anthropic (Claude)**: lectura de documentos
- **n8n** (heredado): flujo antiguo de OCR externo que envía datos ya extraídos a la API

### Stack tecnológico

- **Backend**: Python 3.12, Django 6.1, Django REST Framework 3.18
- **Frontend**: plantillas Django, JavaScript vanilla y CSS propio con tema oscuro (sin frameworks); Font Awesome, fuente Inter y pdf.js por CDN
- **Base de datos**: PostgreSQL 16
- **IA / OCR**: SDK `anthropic` con salida estructurada mediante Pydantic; Pillow, pillow-heif y pypdf para preparar archivos
- **Cola de tareas**: Celery 5.7 + Redis 7
- **Almacenamiento**: django-storages + boto3 contra Cloudflare R2
- **Exportación**: openpyxl (Excel), WeasyPrint (PDF), babel (fechas en español)
- **Servidor**: Gunicorn + WhiteNoise para estáticos
- **Tests**: pytest, pytest-django, factory_boy y pytest-cov (unos 775 tests)

### Seguridad y privacidad

- Todos los datos se filtran por la empresa del usuario; acceder a datos de otra empresa devuelve 404
- Los archivos se guardan en almacenamiento privado y solo se descargan con sesión iniciada
- Los logs registran solo identificadores, nunca NIF, nombres ni claves
- Las API keys se guardan hasheadas y se rechazan si la empresa está inactiva
- Las exportaciones neutralizan la inyección de fórmulas en CSV/Excel
- Retención de documentos configurable (10 años, provisional)

### Entorno de desarrollo

Todo el desarrollo se hace en Docker: un solo comando levanta Postgres, Redis, la web y el worker.

- `docker compose up --build` → la app queda en `http://localhost:8000` y aplica migraciones al arrancar
- `docker compose exec web python manage.py createsuperuser` → crear un administrador
- `docker compose exec web pytest` → ejecutar los tests (no usan red ni claves reales)
- `docker compose build` → obligatorio tras cambiar `requirements.txt`
- `docker compose restart worker` → el worker no recarga solo al cambiar código

La configuración se lee de un archivo `.env`. Variables principales: `SECRET_KEY`, `ALLOWED_HOSTS`, `POSTGRES_*`, `R2_*`, `ANTHROPIC_API_KEY` y `CELERY_BROKER_URL`.

### Despliegue

- Se despliega con **Docker Compose**, con tres variantes:
  - `docker-compose.yml`: desarrollo
  - `docker-compose.prod.yml`: producción con Postgres propio
  - `docker-compose.easypanel.yml`: producción en **Easypanel**, usando su Postgres gestionado (web + Redis + worker)
- Al arrancar, `entrypoint.sh` ejecuta `collectstatic` y `migrate` y lanza Gunicorn; el contenedor del worker usa la misma imagen con otro comando
- En producción se usa `billing_ai.settings.production`: HTTPS, Postgres, sin herramientas de depuración y **R2 obligatorio** (no arranca sin credenciales)
- Plantillas de variables: `.env.prod.example` y `.env.easypanel.example`

### Operación habitual

Comandos de gestión útiles (anteponer `docker compose exec web`):

- `python manage.py enqueue_extraction` → encolar documentos pendientes o fallidos
- `python manage.py submit_batch` / `collect_batch` → enviar y recoger lotes de la Batch API
- `python manage.py extraction_cost` → consultar el coste de IA por cliente y periodo
- `python manage.py merge_companies` → fusionar terceros duplicados
- `python manage.py purge_expired` → borrar documentos caducados (sin `--apply` solo simula)
- `python manage.py import_n8n` → importar datos del flujo antiguo
- `python manage.py n8n_exit_report` → comprobar si ya se puede apagar n8n

### Pendiente y próximos pasos

- Calibrar los umbrales de auto-aprobación con datos reales
- Probar la Batch API con una clave real
- Firmar los acuerdos de tratamiento de datos (Anthropic, Cloudflare) antes de procesar facturas reales
- Confirmar el periodo de retención con el asesor
- Gestión de etiquetas, mover carpetas manuales y asignación masiva desde las tablas
- Usar los roles de usuario para controlar permisos
- Retirar n8n cuando el nuevo flujo lo cubra por completo

### Para saber más

- `CLAUDE.md` (raíz): referencia técnica detallada (modelos, reglas del pipeline, convenciones de código y tests)
- `docs/plan-ocr.md`: plan original de la migración a OCR y registro de decisiones por hito (H0–H10)
- `documents/services/`: lógica de negocio, punto de partida para entender cualquier flujo
