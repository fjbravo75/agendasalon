# Supuestos de uso demostrables en Peluquería Mari y Barbería Norte

Este documento explica los personajes ficticios de la semilla académica y qué
capacidad real de AgendaSalon demuestra cada uno. Los nombres, teléfonos y
relaciones no corresponden a personas reales.

Las credenciales se agrupan por ámbito para facilitar la evaluación:

- superadministración: `AgendaSalonDemo1`;
- Peluquería Mari, tanto profesional como clientes: `AgendaSalonDemo2`;
- Barbería Norte, tanto profesional como clientes: `AgendaSalonDemo3`.

Son credenciales públicas y temporales del entorno académico. No corresponden
a cuentas personales ni deben reutilizarse fuera de esta demostración.

## Radiografía del escenario canónico

La demo contiene 2 negocios, 3 cuentas internas, 28 servicios —14 por
negocio—, 36 fichas de cliente —22 en Peluquería Mari y 14 en Barbería Norte—,
11 accesos cliente y 4 relaciones de representación. Sus 90 citas se reparten
en 37 atendidas, 6 no presentadas, 9 canceladas y 38 confirmadas. Hay ejemplos
de teléfono, WhatsApp, correo, mostrador y web; 30 citas proceden de la reserva
web y 8 fueron solicitadas para otra persona autorizada.

Las fechas no están fijadas a una semana envejecida. En cada regeneración, el
histórico se sitúa en los días laborables anteriores y las citas futuras en los
siguientes días laborables disponibles. Así se mantienen demostrables tanto la
actividad pasada como la agenda próxima.

## Personajes de la demo

- **María López** tiene ficha propia y cuenta online activa. Puede reservar para
  sí misma y para su hijo Lucas.
- **Lucas López** tiene una ficha distinta, sin teléfono propio y sin cuenta
  online. Su historial y sus citas pertenecen siempre a Lucas.
- **Daniel Vega** tiene ficha propia y cuenta online activa. Es el cuidador
  autorizado de Rosa Martín.
- **Rosa Martín** tiene una ficha distinta y no necesita cuenta online porque
  Daniel puede reservar por ella.
- **Lucía Gómez** tiene ficha y cuenta propias. Su madre, Ana Gómez, consta como
  contacto externo: puede pedir citas por teléfono o en el local, pero no
  reservar online.
- **Carmen Ruiz** representa una clienta corriente sin relaciones delegadas.
- **Isabel Torres** tiene acceso propio y puede reservar para su madre, Teresa
  García.
- **Óscar Cabrera** tiene acceso en Barbería Norte y puede reservar para su hijo
  Nico.

El resto de las fichas aporta volumen realista a búsquedas, listados, estados y
agenda sin convertir cada persona ficticia en un caso de uso distinto.

## Caso 1. Una madre reserva para ella y para su hijo

María y Lucas aparecen como dos clientes distintos. La ficha de Lucas muestra a
María como `Madre`, contacto principal y persona autorizada para reservar
online.

La semilla crea dos citas de corte a la misma hora del primer día laborable
futuro de la demostración:

- María ocupa la Línea 1 y la reserva figura como realizada para sí misma.
- Lucas ocupa la Línea 2 y la cita conserva que María López la solicitó como
  madre.

El ejemplo demuestra que dos personas pueden acudir juntas sin compartir ficha,
historial ni cita. También demuestra que el menor no necesita teléfono, correo
ni contraseña.

Al entrar en la reserva pública con el teléfono `600111201`, el selector
`¿Para quién es la cita?` ofrece únicamente `María López (yo)` y `Lucas López`.

## Caso 2. Un cuidador reserva para otra persona

Daniel Vega y Rosa Martín también conservan fichas independientes. En la ficha
de Rosa, Daniel aparece como `Cuidador` y tiene autorización online activa.

Una de las citas futuras de Rosa queda asociada a su ficha, mientras
que el detalle profesional conserva que fue solicitada online por Daniel Vega en
calidad de cuidador.

Al entrar con el teléfono `600111204`, Daniel puede elegir entre su propia ficha
y la de Rosa. El permiso solo funciona dentro de Peluquería Mari y puede pausarse
o retirarse desde el panel profesional.

## Caso 3. Una persona puede pedir cita sin acceso online

La ficha de Lucía Gómez conserva a Ana Gómez como madre y contacto principal,
pero Ana es un contacto externo: no tiene ficha propia ni cuenta online en el
negocio.

Este caso permite explicar la diferencia entre estar autorizada para llamar o
pedir una cita en el mostrador y disponer de permiso digital. AgendaSalon no
convierte un número de teléfono en una cuenta ni concede acceso online de forma
automática.

## Caso 4. La misma regla funciona en los dos negocios

Isabel y Teresa reproducen en Peluquería Mari una relación de hija y madre.
Óscar y Nico demuestran en Barbería Norte que el permiso no depende del tipo de
salón: Nico conserva su propia ficha y su cita infantil, mientras que la reserva
recuerda que la solicitó su padre. Una autorización nunca cruza de un negocio a
otro.

## Qué puede comprobar el evaluador

Con la cuenta profesional de Peluquería Mari:

1. Abrir `Clientes` y buscar `Lucas López`.
2. Comprobar que la ficha no tiene teléfono ni cuenta online propia.
3. Ver en `Pueden pedir cita` a María López como madre con reserva online.
4. Abrir `Rosa Martín` y comprobar el mismo recorrido con Daniel Vega como
   cuidador.
5. Abrir las citas sembradas de Lucas y Rosa para ver quién las solicitó y qué
   relación tenía con la persona atendida.
6. Revisar los 14 servicios de Peluquería Mari, incluidos los dos pausados, y
   comprobar el histórico de citas atendidas, canceladas y no presentadas.

Con la cuenta profesional de Barbería Norte:

1. Abrir `Clientes` y buscar `Nico Cabrera`.
2. Comprobar la autorización de Óscar Cabrera como padre.
3. Abrir una cita de Nico y verificar quién la solicitó.
4. Revisar sus 14 servicios y la agenda futura generada con datos propios de la
   barbería.

Con las cuentas cliente:

1. Entrar en `/clientes/peluqueria-mari/entrar/` como María o Daniel.
2. Preparar una reserva desde `/reservar/peluqueria-mari/`.
3. Llegar a la revisión final sin confirmarla.
4. Comprobar que el selector solo muestra la ficha propia y la persona para la
   que existe una autorización expresa.

Óscar puede repetir el mismo recorrido desde
`/clientes/barberia-norte/entrar/` y `/reservar/barberia-norte/`.

## Regeneración manual de la demo pública

La regeneración es una acción manual y protegida del superadministrador. Borra
los cambios mutables realizados durante una evaluación y reconstruye este
escenario ficticio. También retira negocios, cuentas, citas, sesiones,
solicitudes y medios añadidos fuera del contrato canónico. Conserva la
documentación legal publicada, la foto oficial íntegra del BOE, el historial de
copias y los recibos técnicos de regeneración.

La aceptación final del 18 de julio de 2026 utilizó la fecha base
`2026-07-18`, la solicitud `f3a7d392-b728-4206-908c-36ae2320d951` y la huella
semántica
`f53e8ba21674fce64ed4944f90a1d359e717207e8bf4270529506b740a4fcdd8`. El
postflight comprobó 3 cuentas internas, 2 negocios, 28 servicios —25 activos—,
36 fichas de cliente, 90 citas y 8 festivos nacionales oficiales de 2026. La
outbox, las sesiones, los límites temporales de seguridad y los residuos de
evaluación quedaron a cero.

El despachador de solicitudes manuales permanece habilitado y activo. La unidad
histórica que programaba una regeneración diaria a las `04:05 Europe/Madrid` se
conserva para trazabilidad y una eventual reversión deliberada, pero está
deshabilitada e inactiva: la demo no se borra por horario.

## Límites conscientes

La demo no permite que una cuenta cliente cree por sí sola nuevas fichas de
familiares o dependientes. La ficha la crea el negocio y el permiso se concede
desde el panel profesional. Tampoco existe una cesta familiar: dos personas
requieren dos citas, aunque puedan quedar a la misma hora si existen dos líneas
de trabajo libres. Al ser un entorno que se regenera, no debe utilizarse para
guardar datos reales ni trabajo que deba conservarse.
