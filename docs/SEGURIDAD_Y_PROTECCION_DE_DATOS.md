# Seguridad y protección de datos

## Propósito y alcance

Este documento reúne las medidas de seguridad aplicadas en AgendaSalon y las
evidencias que permiten verificarlas. Está preparado como base del apartado de
seguridad de la memoria del Proyecto Fin de Máster.

La revisión distingue tres estados:

- **Aplicado y verificado**: el control está implementado y dispone de pruebas o
  comprobaciones reproducibles.
- **Verificado en despliegue**: el control se ha comprobado también sobre la URL
  pública con HTTPS.
- **Pendiente de operación**: depende de infraestructura, automatización o
  procedimientos que no deben fingirse en el entorno local.

Fecha de la evidencia local más reciente: **18 de julio de 2026**.

La evidencia de publicación no se infiere de la fecha de este documento: debe
comprobarse por SHA exacto, resultado de CI y registro operativo del despliegue.

> **Estado vigente verificado en despliegue.** La versión funcional desplegada
> corresponde a `714a2a22a154b102f31140bc935c4e987c0a5d7e`. La CI
> `29625418697` terminó correctamente en sus cuatro trabajos. `main` puede
> incorporar commits documentales posteriores sin cambiar ese código. La regeneración
> manual aceptada tiene identificador
> `682f8572-de61-4140-b1f5-41a2118b233a`, fecha base `2026-07-18` y huella
> `72d5cef99921795738b707ff02009364110fb1bbdc59d16c4ef7131cc9eb93c0`.
> El temporizador nocturno está habilitado y activo, con siguiente ejecución el
> 19 de julio a las 04:05; todavía no existe una primera ejecución automática
> observada.

> **Antecedente P2 verificado en despliegue.** El SHA funcional
> `ed07e8e1d47eb55620df297636cd26ee10fe25c3` está publicado y aceptado. La PR
> #10 y la ejecución de CI `29589984747`, correcta en todas sus puertas, vinculan
> la implementación con la evidencia reproducible. La aceptación de producción
> fue de solo lectura y no dejó datos de prueba.

P1 se conserva como antecedente publicado: su SHA funcional
`105531945452b5529be6891ee47034c164e804f3`, las PR #7 y #8 y las ejecuciones de
CI `29573943958` y `29574584566` acreditan su cierre anterior.

El cierre de P1 quedó protegido por la copia fría
`agendasalon-20260717T105047Z`, el snapshot
`pre-agendasalon-p1-robustez-2026-07-17-1051Z` (ID `237297105`, acción
`3295909145`, creado el 17 de julio de 2026 a las 10:51:55 UTC) y la copia
posterior `agendasalon-20260717T105901Z`. Se conservaron exactamente 2 negocios,
3 usuarios, 8 clientes, 4 accesos, 23 citas, 5 sesiones, outbox vacío y ninguna
solicitud de alta; los libros legales mantuvieron sus correspondencias 6/6 y
8/8 y las 23 citas históricas conservaron a `null` su referencia pública.
Servicios y temporizadores quedaron activos y el correo se rearmó con una
primera ejecución automática correcta a las 11:11:27 UTC: 0 procesados,
enviados, reprogramados, fallidos y cancelados.

El despliegue de P2 quedó protegido por el snapshot
`pre-agendasalon-p2-experiencia-2026-07-17-1512Z` (ID `237312606`) y por la
copia posterior verificada `agendasalon-20260717T153403Z`. En aquella
aceptación, producción conservó 2 negocios, 3 usuarios, 8 clientes, 4 accesos y
23 citas; mantuvo 2 sesiones activas y 0 caducadas, sin solicitudes de alta,
mensajes en outbox ni revisiones de citas afectadas por festivos.

## Arquitectura de seguridad

```mermaid
flowchart LR
    C["Cliente final"]
    P["Profesional del negocio"]
    S["Superadministrador funcional"]
    T["Personal técnico autorizado"]
    E["Nginx HTTPS<br/>desplegado"]
    D["Aplicación Django"]
    R["Islas React<br/>solo datos JSON protegidos"]
    DB[("PostgreSQL<br/>obligatorio en producción")]
    M[("Medios WebP<br/>recodificados")]
    B["Copia verificada<br/>PostgreSQL + media"]
    X["Destino externo cifrado<br/>pendiente de operación"]

    C --> E
    P --> E
    S --> E
    T --> E
    E --> D
    D --> R
    D --> DB
    D --> M
    DB --> B
    M --> B
    B -.-> X
```

La aplicación separa cuatro superficies:

1. **Reserva pública por negocio**: permite explorar servicios y huecos sin
   mostrar la agenda interna. Exige una cuenta de cliente para confirmar.
2. **Panel profesional**: opera exclusivamente sobre el negocio asociado a la
   pertenencia activa del usuario.
3. **Panel superadministrador**: gestiona el ciclo de vida de los negocios, pero
   no actúa como profesional ni entra en la reserva de sus clientes.
4. **Django Admin**: herramienta técnica bajo `/admin/`, separada del producto y
   reservada a cuentas `is_staff` con permisos de modelo.

## Matriz de controles y evidencias

| Área | Control aplicado | Evidencia principal | Estado |
| --- | --- | --- | --- |
| Autenticación interna | Usuario Django propio con teléfono normalizado, sesión de Django y acceso condicionado por rol y pertenencia activa | `apps/accounts/`, `apps/accounts/tests.py` | Aplicado y verificado |
| Autenticación cliente | Cuenta ligada a una ficha y a un negocio; correo verificado como identidad canónica, teléfono solo como contacto o compatibilidad cuando no es ambiguo; sesión separada con rotación, caducidad y huella de contraseña | `apps/customers/services.py`, `apps/customers/views.py` | Verificado en despliegue P1 |
| Hashing | Argon2id como algoritmo preferente; actualización transparente de hashes PBKDF2 después de un acceso correcto | `config/settings/base.py`, pruebas de `apps/customers/tests.py` | Aplicado y verificado |
| Contraseñas | Mínimo de 12 caracteres y validadores de similitud, contraseñas comunes y valores exclusivamente numéricos | `config/settings/base.py`, formularios y pruebas de acceso | Aplicado y verificado |
| Activación profesional | Los accesos nuevos permanecen inactivos y sin contraseña utilizable hasta que la persona abre un enlace de un solo uso, verifica su correo y crea su propia contraseña; la contraseña temporal queda limitada a compatibilidad heredada | `apps/accounts`, `apps/notifications`, middleware, formularios y pruebas | Aplicado y verificado |
| Verificación de correo profesional | GET y HEAD solo validan y presentan; POST con CSRF confirma. El token específico no depende de `last_login`, pero sí de la contraseña, el correo y el estado de verificación | `apps/accounts/tokens.py`, vistas, notificaciones y pruebas | Verificado en despliegue P2 |
| Verificación de correo cliente | El alta y la invitación dejan el acceso sin contraseña utilizable; GET solo valida y presenta, y POST con CSRF confirma el correo, la privacidad aplicable y la clave; el alta pública mantiene ficha inactiva y `is_pending_public_registration` hasta completar ese POST | `apps/customers`, `apps/notifications` | Verificado en despliegue P1 |
| Retención del alta pública pendiente | Caducidad lógica explícita a las 48 horas, purga segura independiente cada quince minutos y limpieza de sesiones caducadas cada seis horas; las excepciones de seguridad pueden aplazar el borrado físico | `apps/customers`, migración `0015`, comandos y unidades `ops/systemd/` | Verificado en despliegue P2; temporizadores activos |
| Fuerza bruta y enumeración | Limitación por identidad e IP con claves seudonimizadas; alta, reenvío y recuperación aplican esperas o cupos y respuestas genéricas que no confirman cuentas | `apps/core/security_throttle.py`, `apps/customers` | Verificado en despliegue P1 |
| Invitación cliente | Token aleatorio de un solo uso, ligado a negocio y ficha, caducidad de 24 horas y almacenamiento exclusivo de su resumen SHA-256 | `apps/customers/services.py` | Verificado en despliegue P1 |
| Recuperación de contraseña cliente | Solicitud por correo verificado con respuesta genérica; enlace firmado, ligado al negocio y a la huella de contraseña, con caducidad de 60 minutos e invalidación tras cambiar la clave | `apps/customers`, `apps/notifications` | Verificado en despliegue P1 |
| Autorización | Decoradores de acceso, negocio activo en la operativa y filtrado de objetos por empresa; privacidad y derechos son la excepción legal explícita durante una pausa | vistas, API y pruebas de aislamiento | Verificado en despliegue P1 |
| Aislamiento multiempresa | Los endpoints profesionales resuelven el negocio desde la sesión; no confían en un identificador de empresa enviado por el navegador | `apps/booking/api.py`, `apps/dashboards/api.py`, pruebas por negocio | Aplicado y verificado |
| CSRF | `CsrfViewMiddleware`, token en formularios y mutaciones mediante POST. En activación profesional, alta, invitación y recuperación cliente, y verificación posterior del correo profesional, GET y HEAD solo validan o presentan; POST es la única operación que confirma o modifica estado. Las rutas tokenizadas usan `strict-origin` o `no-referrer` según presenten o no un formulario | `config/settings/base.py`, `apps/accounts`, `apps/customers`, plantillas y pruebas con CSRF real | Verificado en despliegue P2 |
| XSS y contenido activo | Autoescape de plantillas, ausencia de inserciones HTML inseguras en el código de producto y CSP con scripts limitados al mismo origen | `apps/core/middleware.py`, `config/settings/base.py` | Aplicado y verificado |
| Cabeceras de navegador | `Permissions-Policy`, CORP `same-origin`, bloqueo de marcos y objetos mediante CSP y política de referencia diferenciada entre formularios POST y respuestas de token sin formulario | middleware, vistas y pruebas de cabeceras | Aplicado y verificado |
| Validación | Formularios Django, `full_clean()`, normalización de teléfonos, restricciones de modelos y mensajes genéricos en accesos sensibles | formularios, modelos y batería indicada en «Evidencias reproducibles» | Verificado en despliegue P2 |
| Integridad de citas | Revalidación del hueco, duraciones compatibles con el intervalo, cierre solo tras `ends_at` y bloqueos comunes entre confirmación y mutaciones profesionales de horarios, cierres, preferencia de festivos o líneas | `apps/booking/services.py`, modelos y vistas | Verificado en despliegue P1 |
| Idempotencia de reserva pública | Cada borrador nuevo lleva una referencia UUID única y anulable en la cita; el replay se resuelve bajo el mutex del calendario y devuelve la cita ya creada sin repetir actividad ni outbox. Los borradores heredados de P0, sin referencia, se descartan y obligan a elegir de nuevo | `apps/booking/public_booking_drafts.py`, `Appointment.public_confirmation_reference`, migración `booking.0007` y pruebas SQLite/PostgreSQL | Verificado en despliegue P1 |
| Trazabilidad familiar | La cita distingue receptor y solicitante autorizado, regenera opciones por cliente, invalida resultados obsoletos y conserva instantáneas, línea y hora exactas | `apps/booking`, isla React y sincronización del asistente | Verificado en despliegue P1 |
| Continuidad de privacidad | Una nueva versión exige nueva constancia; privacidad y derechos siguen accesibles con el negocio pausado sin reabrir reserva ni registro | `apps/legal`, `apps/booking`, `apps/customers` | Verificado en despliegue P1 |
| Evidencia legal exacta | Recibo firmado y temporal con finalidad, audiencia, documento, versión, huella y contexto; proyección vigente más libros de eventos de solo adición y escritura transaccional | `apps/legal/presentations.py`, modelos, migraciones y pruebas | Verificado en despliegue P1 |
| Administración técnica | Agenda, calendario, festivos, evidencias legales y correo se muestran en Django Admin como solo lectura, sin altas, ediciones, borrados ni acciones masivas; las solicitudes de derechos solo admiten seguimiento de estado y nota, sin alta ni borrado | módulos `admin.py` y pruebas de permisos | Verificado en despliegue P1 |
| Outbox concurrente | Reclamación mediante `lease` temporal, recuperación de trabajos caducados, latido continuo durante SMTP, cancelación coordinada y cierre exclusivo por el propietario vigente; se documenta el residual SMTP de entrega al menos una vez | `apps/notifications` y pruebas PostgreSQL | Verificado en despliegue P1; residual SMTP conservado |
| Avisos operativos configurables | Destinos separados para plataforma y negocio, verificación firmada y de un solo uso, preferencias, límites por identidad/IP/destinatario y máximos globales; las trazas excluyen correo de destino, tokens, cuerpos y contexto funcional sensible, pero conservan `actor_user` y `actor_label` para auditoría; los formularios guardan por campos para evitar sobrescrituras entre apariencia y avisos | `apps/notifications`, `PlatformActivityEvent`, formularios y pruebas SQLite/PostgreSQL | Aplicado y verificado localmente; publicación y activación pendientes |
| Solicitud manual de regeneración | Acción en dos pantallas, contraseña actual, frase exacta, confirmación, CSRF, throttling, solicitud activa única, estados terminales y digest opaco del origen; la petición HTTP nunca ejecuta el borrado y el resultado se acredita mediante recibo preservado | `DemoRefreshRequest`, `apps/dashboards/demo_refresh.py`, formularios, vistas, despachador root y pruebas SQLite/PostgreSQL | Aplicado y verificado localmente; publicación y aceptación pendientes |
| Sincronización BOE | Exclusión mutua por año antes de la consulta externa; después de la descarga, `SHARE` sobre el registro de negocios, cooperación `ROW EXCLUSIVE` de las mutaciones, agendas en orden estable, reconciliación atómica, fotografía de impacto y altas concurrentes incluidas | `apps/holidays`, mutex de calendario y pruebas PostgreSQL/BOE | Verificado en despliegue P1 |
| Revisión de citas en festivo | Bandeja privada calculada desde el estado vivo, agregado superadministrador sin datos personales y confirmación manual idempotente que no mueve, cancela ni envía mensajes | `apps/holidays`, `apps/booking`, vistas y pruebas SQLite/PostgreSQL | Verificado en despliegue P2 |
| Subida de imágenes | JPG, PNG o WebP; 5 MB y 16 millones de píxeles; orientación, reducción a 2400 px y recodificación WebP sin EXIF | `apps/businesses/images.py`, pruebas de ajustes | Aplicado y verificado |
| Galería pública por negocio | Las imágenes propias se relacionan con un único negocio y el formulario solo permite seleccionar archivos de esa misma empresa | `BusinessPublicImage`, formulario de ajustes y pruebas de aislamiento | Aplicado y verificado |
| Secretos | Variables de entorno obligatorias en producción; arranque detenido si faltan secreto, hosts o PostgreSQL | `config/settings/prod.py`, `.env.example`, pruebas de producción | Aplicado y verificado |
| Base de datos | SQLite solo para desarrollo; PostgreSQL obligatorio en producción, conexión persistente con comprobación de salud | `config/settings/database.py`, `config/settings/prod.py` | Aplicado y verificado |
| Regeneración académica | Borrado integral limitado al modo demo mediante confirmación explícita, identidad exacta del entorno, quiescencia, exclusión de conexiones, transacción PostgreSQL, cuarentena de medios, supresión SMTP y postflight sin residuos | `apps/core/demo_integrity.py`, `refresh_demo`, `ops/run_demo_refresh.sh` y unidades systemd | Aceptación manual verificada; primer disparo automático pendiente |
| HTTPS | Redirección a HTTPS, cookies seguras, orígenes CSRF configurables y HSTS inicial | `config/settings/prod.py` y validación pública del 14-07-2026 | Verificado en despliegue |
| Dependencias | Versiones fijadas; auditorías Python y Node sin vulnerabilidades conocidas en la fecha de revisión | `requirements.txt`, `package-lock.json`, comandos de evidencia | Aplicado y verificado |
| Copias | Copia diaria de PostgreSQL y `media`, hashes SHA-256, manifiesto HMAC, retención 7/4/6 y control de frescura inferior a 36 horas | `ops/backup_restore.py`, `ops/test_backup_restore.py`, `ops/systemd/` | Verificado en despliegue |
| Destino externo de copias | Retención definida y requisito de almacenamiento cifrado fuera del servidor | `docs/OPERACION_PRODUCCION.md` | Pendiente de operación |

## Autenticación, sesiones y contraseñas

El acceso profesional y superadministrador utiliza el sistema de autenticación
de Django sobre un usuario personalizado. El teléfono se normaliza antes de
identificar la cuenta, evitando que diferentes formatos representen identidades
distintas.

Los clientes no comparten una cuenta global entre salones. Cada acceso queda
ligado a un negocio y a una ficha concreta, y su identidad digital canónica es
el correo verificado en ese negocio. El teléfono es un dato de contacto; solo se
admite como compatibilidad de acceso si identifica una única cuenta verificada.
Si hay más de una coincidencia, el sistema no escoge una cuenta por orden de base
de datos y mantiene una respuesta genérica.

El registro público siempre crea una ficha nueva y nunca reclama una ficha
profesional a partir del nombre o del teléfono. Una coincidencia en esos datos no
bloquea el alta ni revela pertenencia. La creación desde el panel profesional
conserva, por separado, la unicidad/reutilización de la ficha activa con el mismo
nombre y teléfono normalizados, y puede emitir una invitación dirigida a esa
ficha.

El alta pública y la invitación dejan la cuenta pendiente, sin contraseña
utilizable. El GET del enlace firmado es deliberadamente no mutante: comprueba
que el paso sigue disponible y muestra el formulario. Solo el POST protegido
por CSRF confirma el correo, la privacidad aplicable y la contraseña elegida.
El alta pública mantiene además la ficha inactiva y
`is_pending_public_registration=True` hasta completar ese POST; si el negocio
pausa entretanto las altas, no se activa. Las invitaciones
emplean un nonce aleatorio del que solo se almacena el resumen; la verificación y
el restablecimiento quedan ligados a la cuenta, el negocio, el correo y la
huella de la credencial vigente. El consumo del enlace o un cambio posterior de
credencial impiden reutilizarlo.

P2 añade una caducidad lógica explícita para las altas públicas
pendientes: 48 horas desde su creación o desde el último enlace realmente
encolado que renueve el plazo. La purga posterior se ejecuta de forma
independiente y solo elimina el grafo cuando no existe actividad, evidencia,
relación protegida ni envío activo que deba conservarse. Por ello, las 48 horas
no representan un máximo físico de conservación. El contrato operativo completo,
incluidos la doble pasada de un `lease` caducado, el límite útil por lotes, el
backfill de `customers.0015` y la precondición de migración sobre P1, se mantiene
en [Operación en producción](OPERACION_PRODUCCION.md#caducidad-de-altas-públicas-pendientes).

Las contraseñas nuevas se almacenan con Argon2id. Django conserva PBKDF2 como
algoritmo compatible para poder verificar cuentas antiguas y actualizar su hash
después de un acceso correcto. Nunca se guardan contraseñas en claro.

El alta profesional normal crea una cuenta inactiva y sin contraseña utilizable;
la persona define su clave desde el enlace de correo. La credencial temporal y
su indicador persistente se conservan solo para cuentas heredadas o
intervenciones administrativas controladas. En esos casos, un middleware situado
antes del onboarding legal impide entrar en agenda, clientes o configuración
hasta sustituirla. `Mi cuenta` permite cambios posteriores verificando la contraseña actual
y rechazando una nueva contraseña idéntica. `update_session_auth_hash()` conserva
la sesión presente; el cambio del hash de contraseña invalida las demás sesiones.
Los parámetros de retorno se validan contra el host y esquema actuales para
evitar redirecciones externas.

La verificación posterior del correo profesional utiliza un generador específico
que no incorpora `last_login` a su huella. Por eso el enlace sobrevive a un inicio
o cierre de sesión, pero deja de ser válido si cambia la contraseña, el correo
normalizado o el estado de verificación, si se consume o si caduca. GET y HEAD
solo presentan el paso; la confirmación exige POST con CSRF. Se conserva una
comprobación heredada acotada para enlaces anteriores que todavía sean válidos.

Las sesiones usan cookies `HttpOnly` y `SameSite=Lax`. En producción se marcan
además como `Secure`. La sesión cliente rota su identificador al entrar y salir,
y el acceso caduca tras una hora sin actividad. También conserva una huella
HMAC de la contraseña: si la clave cambia, las sesiones anteriores dejan de ser
válidas. El restablecimiento cliente parte de una solicitud por correo
verificado, no revela si existe una cuenta y genera, cuando corresponde, un
enlace limitado al negocio con 60 minutos de vigencia.

El cambio del correo canónico desde la gestión profesional invalida la
verificación anterior, retira la contraseña vigente y, por esas dos condiciones,
cierra las sesiones existentes. La persona debe verificar la nueva dirección y
crear otra contraseña antes de recuperar la operativa digital. El alta, el
reenvío de verificación y la recuperación se limitan por las claves pertinentes
de correo, teléfono e IP y no modifican sus respuestas para confirmar la
existencia de una cuenta.

## Autorización y aislamiento por negocio

AgendaSalon aplica autorización en dos capas:

- la ruta exige una sesión y un rol válidos;
- cada consulta limita los objetos al negocio resuelto desde la pertenencia del
  usuario o desde el slug público correspondiente.

Las islas React no reciben acceso directo a la base de datos. Consumen endpoints
JSON de solo lectura, protegidos por sesión y con política de no caché. El
identificador del negocio no se acepta como fuente de autorización desde el
navegador.

El panel superadministrador y Django Admin no son equivalentes. El primero
pertenece al producto. El segundo es una consola técnica de mantenimiento: una
cuenta `is_staff` puede entrar, pero Django limita después cada modelo por
permisos; solo el superusuario dispone de acceso completo.

## CSRF, XSS y cabeceras

Las mutaciones de los formularios construidos utilizan POST y token CSRF. El
middleware de Django valida el origen y el token antes de ejecutar la acción.
En la activación profesional, en la verificación posterior de su correo y en los
enlaces cliente de alta, invitación, verificación y recuperación, GET y HEAD solo
presentan y validan el estado del enlace. No confirman correos ni crean o cambian
contraseñas; solo el POST protegido por CSRF ejecuta la mutación.

Las respuestas con formularios POST ordinarios usan
`Referrer-Policy: same-origin`. Las páginas cuyo propio URL contiene un token de
verificación o recuperación usan `strict-origin`: preservan el origen necesario
para que Django valide CSRF sin enviar la ruta ni el token como referencia. Las
respuestas con token que no presentan formulario usan `no-referrer`. En el flujo
profesional, el formulario de activación combina `strict-origin` con `no-store`;
la redirección final y los estados terminales de activación o verificación usan
`no-referrer` y `no-store`. El token no queda reutilizable desde la caché ni se
propaga como referencia después de completar o invalidar el paso. El rechazo
CSRF global aplica esas mismas dos cabeceras: puede ejecutarse antes de entrar en
la vista y, por tanto, antes de que esta detecte una ruta tokenizada.

Las plantillas utilizan el escape automático de Django. La revisión del código
no encuentra `mark_safe`, filtros `safe`, `dangerouslySetInnerHTML`, `innerHTML`,
`eval` ni ejecución dinámica equivalente en las superficies del producto.

La CSP de producto restringe scripts, conexiones, formularios y recursos al
mismo origen, salvo las fuentes declaradas. Bloquea objetos, marcos y atributos
JavaScript. Django Admin mantiene una excepción `unsafe-inline` únicamente para
scripts bajo `/admin/`, necesaria para su interfaz actual; esta excepción no se
propaga a profesionales ni clientes.

El producto todavía permite estilos inline para soportar valores visuales
calculados en algunas plantillas. Esta concesión no habilita scripts inline y
queda registrada como endurecimiento futuro de la CSP.

## Validación e integridad de la agenda

Los formularios y modelos validan tipos, longitudes, formatos, pertenencia y
reglas de negocio. Los números se normalizan, las citas usan fechas conscientes
de zona horaria y las restricciones de base de datos refuerzan las invariantes
que no deben depender solo de la interfaz.

El intervalo de agenda es una regla de dominio. Cada servicio activo, suma de
servicios y ajuste de duración debe ser compatible con él. Cambiar el intervalo
se rechaza si deja servicios activos incompatibles, y una incoherencia heredada
se muestra como error controlado. Las citas atendidas o no presentadas solo se
pueden cerrar cuando ha llegado `ends_at`, nunca simplemente después de su hora
de inicio.

La disponibilidad mostrada no garantiza por sí sola una reserva. Antes de crear
una cita, el servicio de dominio vuelve a calcular el hueco y lo bloquea dentro
de una transacción. La confirmación y las mutaciones profesionales de horarios,
cierres, líneas y preferencia de festivos adquieren los bloqueos compartidos en
un orden estable y vuelven a consultar el estado protegido. De este modo no pueden
confirmarse simultáneamente una cita y un cambio de capacidad que la deje sin
línea u horario válido.

La sincronización global del BOE adquiere una exclusión mutua por año antes de
consultar la fuente. Después de descargarla, la transacción de PostgreSQL toma un
bloqueo `SHARE` breve sobre el registro de negocios antes de enumerarlo; las
mutaciones de calendario cooperan previamente con `ROW EXCLUSIVE`, y cada
calendario se bloquea en el mismo orden que el motor de citas. Luego reconcilia
atómicamente el catálogo oficial, conserva todas las citas y contabiliza las
potencialmente afectadas. Un negocio creado a la vez debe esperar al commit; su
primera cita ya ve el calendario reconciliado y no queda fuera de la fotografía
global.

P2 añade una bandeja privada calculada desde el estado vivo para que
cada profesional revise exclusivamente sus citas futuras afectadas. El
superadministrador recibe únicamente agregados por negocio, sin datos personales.
La confirmación manual de que una cita se mantiene es idempotente y no la mueve,
cancela ni envía mensajes automáticamente. Esta capa está publicada y aceptada
en producción.

La outbox mantiene un `lease` renovado por latido mientras dura SMTP. Una
cancelación pendiente evita el envío; si el mensaje ya está en proceso, no roba
la reserva al worker: una aceptación posterior queda registrada como enviada y
un fallo posterior termina cancelado sin reintento. El control evita dobles
workers y recuperaciones prematuras, pero SMTP no ofrece idempotencia de extremo
a extremo: una aceptación seguida de timeout o caída antes de persistir el
resultado puede producir otro intento. La garantía honesta sigue siendo entrega
al menos una vez.

En los recorridos familiares se conserva por separado quién recibe el servicio
y quién lo solicita. Las instantáneas de nombre y relación mantienen legible el
historial, y las recomendaciones del calendario trasladan conjuntamente el
instante y la línea seleccionados: no se reconstruye una línea distinta a partir
de la hora.

## Gestión de secretos y configuración de producción

El perfil `config.settings.prod` falla de forma explícita si no recibe:

- `DJANGO_SECRET_KEY`;
- `DJANGO_ALLOWED_HOSTS`;
- `DJANGO_DATABASE_URL`.

También exige declarar el contexto legal. Con
`AGENDA_PLATFORM_LEGAL_DEMO=1`, la aplicación se identifica como demostración
académica sin actividad comercial, exige nombre visible, correo y web, y obliga
a mantener vacíos NIF y domicilio para no inventarlos ni exponer datos
personales. Con `AGENDA_PLATFORM_LEGAL_DEMO=0`, el modo comercial continúa
exigiendo la identidad completa y real. La elección no relaja ninguna medida
técnica del perfil de producción.

La barrera `AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL` solo puede activarse en modo
académico y fuerza un backend de correo nulo. El orquestador de regeneración
exige además `AGENDA_DEMO_REFRESH_ENABLED`, identidad exacta de PostgreSQL,
plataforma y medios, y un marcador de quiescencia válido. Estos indicadores no
convierten el comando en seguro por sí solos: las comprobaciones de tablas,
migraciones, conexiones, BOE y rutas canónicas deben superarse conjuntamente.

Los avisos operativos tienen una barrera independiente:
`AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED=0` oculta la navegación y hace que sus
rutas respondan 404. Sus cupos globales son obligatoriamente positivos y el
diario no puede ser inferior al horario. La regeneración manual tiene otra
barrera independiente: con `AGENDA_MANUAL_DEMO_REFRESH_ENABLED=0` la ruta y la
acción no existen para la interfaz. Al activarla, el indicador tampoco concede
por sí solo capacidad destructiva: siguen siendo obligatorios cuenta
superadministradora única y activa, contraseña actual, frase exacta,
confirmación, CSRF, límites de intento, solicitud activa única y reclamación por
el despachador root bajo el mismo bloqueo del orquestador.

PostgreSQL es obligatorio en producción. La URL de conexión se obtiene del
entorno y no se pasa a la herramienta de copias mediante argumentos visibles en
la lista de procesos. `.env.example` contiene únicamente nombres y ejemplos sin
credenciales reales. Gitleaks no detectó secretos en el historial Git completo
existente en la fecha de revisión ni en los cambios preparados del bloque de
cierre.

## HTTPS: configuración y evidencia pública verificadas

El perfil de producción activa:

- redirección obligatoria a HTTPS;
- cookies de sesión y CSRF seguras;
- HSTS inicial de 60 segundos e inclusión de subdominios;
- `upgrade-insecure-requests` dentro de la CSP;
- lista explícita de hosts y orígenes CSRF.

HTTPS está validado en `agendasalon.brvsoftwarestudio.com`: certificado vigente,
redirección desde HTTP, cookies y cabeceras seguras, recursos estáticos y flujos
de acceso y reserva. `SECURE_HSTS_PRELOAD` permanece desactivado de manera
deliberada. El preload no debe activarse hasta sostener la estabilidad del
dominio y revisar el efecto sobre todos sus subdominios.

## Copias de seguridad y recuperación

La herramienta `ops/backup_restore.py` crea un volcado PostgreSQL, un archivo de
medios y un manifiesto con sumas SHA-256 autenticado con una clave HMAC separada
del almacenamiento. La restauración verifica primero integridad y autenticidad,
exige una confirmación explícita y no sobrescribe medios existentes
silenciosamente.

El ensayo realizado en PostgreSQL 17 restauró una copia en una base limpia y
comparó los recuentos de 2 negocios, 19 citas y 7 clientes. Los objetivos
iniciales son RPO de 24 horas, RTO inferior a 2 horas y retención de 7 copias
diarias, 4 semanales y 6 mensuales.

La primera copia local autenticada y verificada se creó en el despliegue y un
temporizador persistente programa su ejecución diaria. La retención 7/4/6 se
aplica únicamente después de verificar todas las copias gestionadas. Un segundo
temporizador falla si no existe una copia auténtica, íntegra y con menos de 36
horas, y una vigilancia local informa a Fran ante fallos o poco espacio. Estas
medidas quedaron verificadas sobre el Droplet el 14 de julio de 2026. El destino
externo cifrado continúa pendiente; por tanto, la continuidad externa todavía
no está cerrada.

## Protección y minimización de datos

AgendaSalon trata datos identificativos y de contacto necesarios para gestionar
citas. El MVP excluye datos sanitarios. Las notas internas se limitan a
información operativa y no deben utilizarse para almacenar información sensible.

El historial de actividad conserva trazabilidad de acciones sin guardar
contraseñas, tokens en claro ni datos personales innecesarios. La actividad
global del superadministrador no muestra nombres ni teléfonos de clientes.
Las reservas públicas registran el actor genérico `Cliente online` y omiten del
detalle de cambios los nombres de quien solicita o recibe la cita. La migración
`businesses.0009` aplica la misma minimización a los eventos públicos ya
existentes, sin alterar la ficha de cita que necesita el profesional.

La solicitud pública de alta profesional aplica minimización propia: no pide
contraseña, NIF, razón social, dirección completa, horarios, servicios, clientes
ni datos de pago. Conserva el documento de privacidad, su versión, huella y fecha
de lectura. El teléfono se normaliza, los reenvíos equivalentes no duplican una
solicitud abierta y los POST se limitan por teléfono e IP mediante claves
resumidas. Los datos recibidos solo aparecen en la zona superadministradora y no
se copian como contacto público del negocio sin una decisión expresa.

La aceptación de privacidad no se considera permanente para cualquier texto
futuro. Si cambia la versión o la huella del documento vigente, la cuenta debe
registrar una nueva constancia antes de confirmar otra reserva. Además, la
política del negocio y el canal para ejercer derechos permanecen accesibles si
el negocio o la reserva pública están pausados. Esta excepción de continuidad
legal no vuelve a publicar servicios ni habilita la reserva o el registro de
nuevas cuentas.

Los recibos emitidos desde P1 incorporan la identidad legal de plataforma
mostrada. Cualquier cambio en `AGENDA_PLATFORM_LEGAL_NAME`,
`AGENDA_PLATFORM_TAX_ID`, `AGENDA_PLATFORM_LEGAL_ADDRESS`,
`AGENDA_PLATFORM_PRIVACY_EMAIL`, `AGENDA_PLATFORM_WEBSITE` o
`AGENDA_PLATFORM_LEGAL_DEMO` exige rotar las versiones de los documentos
afectados y obtener una nueva aceptación. Las evidencias anteriores a P1
conservan compatibilidad histórica: como solo registraban la identidad del
negocio, siguen comparándose con esa parte mientras no cambie, pero no acreditan
la identidad de plataforma que no capturaron.

La migración `legal.0007`, que incorpora los libros de eventos, está marcada como
irreversible. Una marcha atrás parcial podría borrar evidencia posterior al
despliegue; por eso no se permite sortearla con `--fake`. La reversión segura es
restaurar de forma completa y coherente la copia o el snapshot previo junto con
el SHA de aplicación correspondiente.

Estas medidas técnicas no sustituyen las obligaciones jurídicas de una
explotación comercial. La publicación académica no debe usarse con actividad
comercial ni datos de clientes reales. Antes de activar ese uso deben cerrarse
identidad fiscal real, política de privacidad, base jurídica, información al
usuario, contratos con encargados, plazos definitivos de conservación y
procedimiento de ejercicio de derechos.

La demo pública usa exclusivamente identidades ficticias. Su regeneración
elimina de forma deliberada negocios, clientes, citas, accesos, sesiones,
solicitudes, outbox, evidencias operativas y medios introducidos durante una
evaluación. Este borrado es aceptable únicamente por el contrato académico de
residuo cero; no debe trasladarse a un entorno comercial ni utilizarse con
información real. Los documentos legales publicados, la foto BOE trazable, el
historial de copias y los recibos técnicos se conservan y se comparan mediante
firmas antes y después de la operación.

## Evidencias reproducibles

### Evidencia vigente de escenario y regeneración académica

| Comprobación | Resultado |
| --- | --- |
| SHA de la versión funcional desplegada | `714a2a22a154b102f31140bc935c4e987c0a5d7e` |
| CI | Ejecución `29625418697`, cuatro trabajos correctos |
| Estado canónico | 2 negocios, 3 cuentas internas, 28 servicios, 36 clientes, 11 accesos, 4 relaciones y 90 citas |
| Aceptación manual | Una ejecución correcta el 18-07-2026, con fecha base `2026-07-18` |
| Identificador | `682f8572-de61-4140-b1f5-41a2118b233a` |
| Huella semántica | `72d5cef99921795738b707ff02009364110fb1bbdc59d16c4ef7131cc9eb93c0` |
| Correo durante el refresco | Suprimido por configuración y backend nulo |
| Temporizador | Habilitado y activo; siguiente ejecución el 19-07-2026 a las `04:05 Europe/Madrid`, `Persistent=false` |
| Primera ejecución automática | Pendiente de observación; no acreditada por la prueba manual |

### Evidencia publicada de P2

Los siguientes controles se ejecutaron sobre el árbol definitivo de P2 en un
entorno local y aislado. La PR #10, la ejecución de CI `29589984747` y la
aceptación de producción del SHA funcional
`ed07e8e1d47eb55620df297636cd26ee10fe25c3` completan su evidencia publicada:

```powershell
.\.venv\Scripts\coverage.exe run manage.py test
.\.venv\Scripts\coverage.exe report
npm.cmd run check
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe -m pip_audit
.\.venv\Scripts\python.exe -m pip check
npm.cmd audit --audit-level=high
git diff --check
```

| Comprobación | Resultado |
| --- | --- |
| Suite Django SQLite | 596 pruebas ejecutadas correctamente; 35 casos exclusivos de PostgreSQL omitidos |
| Suite Django PostgreSQL 17 | 596 de 596 pruebas correctas; ninguna omitida |
| Cobertura con ramas | 85 %; puerta mínima automatizada del 82 % |
| Suite frontend | 34 de 34 pruebas correctas |
| Build Vite | Correcto |
| Ruff | Sin incidencias |
| `manage.py check` | Sin incidencias |
| Migraciones | No se detectaron cambios pendientes |
| `pip check` | Sin incompatibilidades conocidas |
| `git diff --check` | Sin errores de espacios ni marcadores |
| CI del P2 | Ejecución `29589984747` correcta en todas sus puertas |
| Despliegue del P2 | SHA funcional `ed07e8e1d47eb55620df297636cd26ee10fe25c3` aceptado mediante GET y solo lectura, sin residuo |
| Protección del despliegue | Snapshot ID `237312606` y copia posterior verificada `agendasalon-20260717T153403Z` |

### Evidencia publicada de P1

Los siguientes resultados permanecen como referencia del bloque funcional P1
publicado y aceptado el 17 de julio de 2026:

| Comprobación | Resultado |
| --- | --- |
| Suite Django SQLite | 534 pruebas correctas; 25 casos exclusivos de PostgreSQL omitidos |
| Suite Django PostgreSQL 17 | 534 de 534 pruebas correctas; ninguna omitida y ninguna base `test_%` residual |
| Cobertura con ramas | 84,16 %; puerta mínima automatizada del 82 % |
| Suite frontend | 34 de 34 pruebas correctas |
| Build Vite | Correcto |
| Ruff | Sin incidencias |
| `manage.py check` | Sin incidencias |
| Migraciones | No se detectaron cambios pendientes |
| `pip-audit` | 0 vulnerabilidades conocidas |
| `npm audit` | 0 vulnerabilidades conocidas |
| `pip check` | Sin incompatibilidades conocidas |
| `git diff --check` | Sin errores de espacios ni marcadores |
| BOE real | Dos ejecuciones idempotentes en entorno efímero, sin alterar citas |
| QA visual y funcional | Apta en escritorio y móvil sobre copia desechable, incluidos recorridos CSRF reales; base canónica intacta |
| Limpieza QA | Sin bases, contenedores, procesos, puertos ni temporales del bloque |
| CI del P1 | Ejecuciones `29573943958` y `29574584566` correctas |
| Despliegue del P1 | SHA funcional `105531945452b5529be6891ee47034c164e804f3` aceptado mediante GET y solo lectura, sin residuo |

Como referencia histórica, P0 quedó validado con 396 pruebas Django, nueve
omisiones, 29 frontend y 83 % de cobertura antes de su publicación. En P1,
Gitleaks sobre el alcance completo no detectó secretos y PostgreSQL 17 forma
parte de la evidencia reproducible. CI, copia, snapshot y despliegue constan
asociados al SHA funcional publicado en el cierre operativo.

El chequeo de producción se ejecutó con valores locales temporales, sin
credenciales reales y sin conectar servicios externos:

```powershell
.\.venv\Scripts\python.exe manage.py check --deploy --settings=config.settings.prod
```

Resultado: una única advertencia, `security.W021`, porque HSTS preload permanece
desactivado hasta disponer de dominio y HTTPS estables.

## Correcciones derivadas del escáner de 13 de julio de 2026

El escáner estándar sellado identificó tres hallazgos bajos y los tres quedan
corregidos en esta versión:

1. La admisión de autenticación reserva de forma atómica los límites de sujeto
   e IP antes de ejecutar Argon2. Los bloqueos se adquieren en orden
   determinista y una autenticación correcta no borra reservas posteriores.
2. Cada negocio retiene como máximo doce imágenes públicas. Como cada salida
   WebP está limitada a 5 MB, el presupuesto agregado queda acotado a 60 MB.
   La comprobación y la creación se serializan sobre la fila del negocio y un
   rollback elimina cualquier archivo ya escrito.
3. Pausar o reactivar un contacto autorizado no reescribe la concesión de
   reserva. Un permiso asociado a contacto solo es efectivo cuando la concesión
   y el contacto están activos; una revocación explícita sobrevive a la
   reactivación.

La reproducción concurrente en PostgreSQL admite exactamente cinco
comprobaciones para doce solicitudes del mismo sujeto y treinta para treinta y
seis sujetos bajo una misma IP. Dos cargas que compiten por el último hueco de
galería conservan una sola. Los PoCs originales confirman el cierre: el de
permisos termina en `PASS` y el que exigía aceptar trece imágenes falla en la
decimotercera solicitud, como corresponde al nuevo control.

## Riesgos residuales vigentes

Las escrituras directas de agenda desde Django Admin, la evidencia legal no
ligada a la versión mostrada y la outbox sin `lease` dejan de figurar como
riesgos residuales de código: P1 los cierra y los valida en local. Su aceptación
desplegada quedó verificada el 17 de julio de 2026 con el SHA funcional
`105531945452b5529be6891ee47034c164e804f3`; la documentación pública y
producción quedaron después alineadas con `1e4c6cdbeaca72ca3df4c6b5c8c0f138ef02f489`.
P2 suma la verificación profesional, la retención de altas pendientes y la
revisión asistida de citas en festivo, ya publicadas y aceptadas con el SHA
funcional `ed07e8e1d47eb55620df297636cd26ee10fe25c3`.

| Riesgo residual | Prioridad | Decisión o condición de cierre |
| --- | --- | --- |
| HTTPS público | Cerrado para la demo | Certificado válido, redirección HTTP, cabeceras, acceso y reserva comprobados en `agendasalon.brvsoftwarestudio.com` |
| Terminación TLS del proxy | Cerrado para la demo | Nginx sobrescribe `X-Forwarded-Proto`, Gunicorn solo escucha en socket y Django confía únicamente en el proxy local declarado |
| Primera ejecución automática del refresco | Pendiente de operación | El servicio ya superó una aceptación manual; el timer está habilitado y activo y debe observarse el primer disparo real previsto para el 19-07-2026 a las 04:05 |
| Copias sin destino externo cifrado | Alta para continuidad; bloqueante para explotación comercial | La retención 7/4/6 y la vigilancia local están activas; falta elegir el destino externo y repetir una restauración desde él |
| Django Admin accesible desde Internet | Alta | Restringir por red, VPN o IP y usar cuentas técnicas personales con privilegios mínimos |
| Resolución asistida de citas afectadas por un festivo importado | Cerrada para la demo en P2 | Bandeja profesional privada, agregado superadministrador sin datos personales y confirmación manual idempotente publicados y aceptados en producción |
| Sin segundo factor para cuentas técnicas | Alta para explotación comercial | Incorporar MFA o proteger el acceso mediante identidad del proveedor o VPN |
| Galería limitada a 12 archivos, pero sin límite temporal de subidas | Media | Añadir límite por cuenta o proxy; pasar el procesamiento a un worker si aumenta el volumen |
| Sin monitorización central de toda la plataforma | Media | La vigilancia de copias y disco ya avisa localmente; falta centralizar disponibilidad, errores y logs del conjunto |
| `unsafe-inline` en scripts de Django Admin | Media y acotada | Mantener la excepción solo en `/admin/` y revisar nonce o hash si se personaliza la consola |
| Estilos inline permitidos en el producto | Baja | Sustituir valores inline por clases o variables controladas y retirar progresivamente `unsafe-inline` de las directivas de estilo |
| Política de privacidad y conservación definitiva no cerradas | Bloqueante para uso real con clientes | Completar la capa jurídica y operativa antes de recopilar datos reales |
| Modo académico utilizado para una actividad comercial | Bloqueante para uso real | Mantener la demo sin actividad comercial ni clientes reales; pasar a modo comercial solo con identidad legal real y revisión jurídica |

## Veredicto

AgendaSalon supera el alcance técnico exigible para explicar autenticación,
hashing, validación, CSRF, XSS, permisos, secretos y copias de seguridad. Los
controles de aplicación están implementados y respaldados por pruebas.

La versión vigente añade un escenario académico realista y un mecanismo de
regeneración protegido. La CI, el SHA común y la aceptación manual acreditan el
estado publicado; el temporizador está habilitado, pero aún no acreditan su
primer disparo automático. Esta distinción se mantiene como parte de la
evidencia y no como un resultado supuesto.

El bloque P0 queda como antecedente histórico en el SHA
`5c68a260d1d87ed00c908d25bf519c3f34fea712`. P1 se conserva como antecedente
publicado: su SHA funcional `105531945452b5529be6891ee47034c164e804f3`
superó 534 pruebas
Django en SQLite y PostgreSQL 17, 34 de 34 frontend, 84,16 % de cobertura y QA
aislada sin residuos. Las PR #7 y #8, sus ejecuciones de CI, las copias, el
snapshot y la aceptación operativa acreditan el despliegue; la PR #9 sincronizó
la documentación y dejó `1e4c6cdbeaca72ca3df4c6b5c8c0f138ef02f489` como SHA final
de `main` y de producción.

P2 queda como antecedente funcional publicado y aceptado: 596 pruebas Django
correctas en PostgreSQL 17, 596 ejecutadas correctamente en SQLite con 35
omisiones exclusivas de PostgreSQL, 34 de 34 pruebas frontend y 85 % de
cobertura con ramas. La PR #10, el CI `29589984747`, el snapshot ID
`237312606`, la copia posterior `agendasalon-20260717T153403Z`, las migraciones,
los temporizadores y la aceptación operativa sin residuos acreditan el SHA
funcional `ed07e8e1d47eb55620df297636cd26ee10fe25c3`.

La aplicación está publicada como **demo académica** y HTTPS, proxy, aislamiento,
copias locales, retención y vigilancia de frescura disponen de evidencia en el
entorno definitivo. No debe presentarse como **lista para explotación
comercial**: el destino externo de copias, la monitorización central, el acceso
técnico reforzado y las obligaciones jurídicas reales siguen pendientes.

El superadministrador dispone ahora de un estado de continuidad verificable y
un historial técnico de solo lectura. Esta visibilidad no cierra el riesgo
residual: mientras no exista una ejecución reciente declarada como externa y
verificada, la propia interfaz mantiene visibles la programación y el destino
externo como pendiente de operación.
