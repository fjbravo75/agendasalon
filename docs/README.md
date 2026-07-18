# Documentación técnica del entregable

Esta carpeta reúne documentación técnica útil de la aplicación
entregable: instalación, despliegue, datos demo, arquitectura y decisiones que
deban viajar con el código.

Documentos disponibles:

- [`SEGURIDAD_Y_PROTECCION_DE_DATOS.md`](SEGURIDAD_Y_PROTECCION_DE_DATOS.md):
  matriz de controles, evidencias reproducibles, arquitectura y riesgos
  residuales.
- [`OPERACION_PRODUCCION.md`](OPERACION_PRODUCCION.md): perfil de producción,
  cabeceras, administración técnica, copias y restauración.
- [`EVIDENCIAS_CANDIDATA_10.md`](EVIDENCIAS_CANDIDATA_10.md): índice de pruebas
  reproducibles, límites y validaciones todavía pendientes de entorno o personas.
- [`validation-professionals/README.md`](validation-professionals/README.md):
  protocolo honesto para sesiones con profesionales reales.
- [`SUPUESTOS_USO_DEMO.md`](SUPUESTOS_USO_DEMO.md): personajes ficticios,
  relaciones familiares y de cuidados y recorrido comprobable por el evaluador.
- [`memoria/Memoria_tecnica_AgendaSalon.docx`](memoria/Memoria_tecnica_AgendaSalon.docx):
  memoria técnica desplegada, con casos de uso, capturas, diagramas, seguridad,
  pruebas, limitaciones y fuentes del proyecto.
- [`memoria/MEMORIA_TECNICA.md`](memoria/MEMORIA_TECNICA.md): fuente Markdown
  editable de la memoria técnica.

## Renderizado documental en Windows

La memoria se mantiene primero en Markdown y se genera como DOCX. Para producir
el PDF y las imágenes de control en Windows se usa el lanzador del proyecto:

```powershell
python tools/render_docx_windows.py `
  docs/memoria/Memoria_tecnica_AgendaSalon.docx `
  --output-dir _visual_checks/memoria
```

El lanzador aísla el perfil de LibreOffice y construye su ruta como URI
`file:///C:/...`, que es el formato correcto en Windows. No se debe modificar
`bootstrap.ini` ni reutilizar la forma inválida `file://C:\...`. La conversión
se completa renderizando cada página como PNG para poder revisar visualmente el
documento antes de darlo por terminado.
