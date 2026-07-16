# Supuestos de uso demostrables en Peluquería Mari

Este documento explica los personajes ficticios de la semilla académica y qué
capacidad real de AgendaSalon demuestra cada uno. Los nombres, teléfonos y
relaciones no corresponden a personas reales.

La contraseña común de las cuentas indicadas en el `README.md` es
`DemoAgendaSalon2026!`.

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

## Caso 1. Una madre reserva para ella y para su hijo

María y Lucas aparecen como dos clientes distintos. La ficha de Lucas muestra a
María como `Madre`, contacto principal y persona autorizada para reservar
online.

La semilla crea dos citas de corte a la misma hora del lunes de demostración:

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

La cita de Rosa del martes de demostración queda asociada a su ficha, mientras
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

## Qué puede comprobar el evaluador

Con la cuenta profesional de Peluquería Mari:

1. Abrir `Clientes` y buscar `Lucas López`.
2. Comprobar que la ficha no tiene teléfono ni cuenta online propia.
3. Ver en `Pueden pedir cita` a María López como madre con reserva online.
4. Abrir `Rosa Martín` y comprobar el mismo recorrido con Daniel Vega como
   cuidador.
5. Abrir las citas sembradas de Lucas y Rosa para ver quién las solicitó y qué
   relación tenía con la persona atendida.

Con las cuentas cliente:

1. Entrar en `/clientes/peluqueria-mari/entrar/` como María o Daniel.
2. Preparar una reserva desde `/reservar/peluqueria-mari/`.
3. Llegar a la revisión final sin confirmarla.
4. Comprobar que el selector solo muestra la ficha propia y la persona para la
   que existe una autorización expresa.

## Límites conscientes

La demo no permite que una cuenta cliente cree por sí sola nuevas fichas de
familiares o dependientes. La ficha la crea el negocio y el permiso se concede
desde el panel profesional. Tampoco existe una cesta familiar: dos personas
requieren dos citas, aunque puedan quedar a la misma hora si existen dos líneas
de trabajo libres.
