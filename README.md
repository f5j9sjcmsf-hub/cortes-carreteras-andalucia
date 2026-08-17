# Carreteras cortadas - Andalucía

Bot independiente de Telegram que vigila todos los cortes completos de
carreteras publicados por la Dirección General de Tráfico en Andalucía, sin
limitarse a una causa concreta.

Este proyecto no comparte bot, canal, repositorio, secretos ni estado con el
bot de incendios forestales. El funcionamiento del bot anterior no se modifica.

## Fuente y alcance

La fuente oficial es la publicación de incidencias
[DATEX II v3.7 de la DGT](https://nap.dgt.es/en/dataset/incidencias-dgt-datex2-v3-7).
El bot incluye únicamente registros activos que DGT publica como alguno de
estos dos tipos DATEX II:

- `roadClosed`: carretera o tramo completo cerrado;
- `carriagewayClosures`: calzada completa cerrada, normalmente todo un sentido
  de una vía dividida. Si el registro informa del uso de carriles, debe indicar
  `allLanesCompleteCarriageway`.

Se excluyen `laneClosures`, los cierres de uno o varios carriles que no abarcan
la calzada completa, las retenciones, la circulación alterna y las
restricciones para clases concretas de vehículos. Por tanto, un sentido con
todos sus carriles cerrados sí se publica; un carril parcial cerrado, no.

Se incluyen todas las causas publicadas por DGT: obras, desprendimientos,
daños en la vía, meteorología, inundaciones, nieve, accidentes, obstáculos y
cualquier categoría futura que acompañe a un cierre completo.

## Sentidos y agrupación

- `negative` en el perfil español de DGT significa sentido kilométrico
  decreciente.
- `positive` significa sentido kilométrico creciente.
- `both` significa doble sentido.
- Dos registros opuestos con la misma vía, tramo, provincia, localidades y
  causa se presentan como un único corte de doble sentido.

La agrupación conserva todos los identificadores DGT originales. Los dos
sentidos continúan siguiéndose por separado aunque se muestren en un único
aviso: si desaparece solo uno, se publica una reapertura parcial y el sentido
restante sigue activo; si desaparecen ambos, se publica la reapertura total. Si
el tramo vuelve a cerrarse después, se anuncia como un nuevo cierre. De este
modo se mantiene el seguimiento completo de cierres, cambios, aperturas
parciales, reaperturas totales y cierres posteriores.

## Avisos

La primera ejecución publica la lista completa de cortes activos. Después solo
se envían mensajes cuando ocurre alguno de estos cambios:

- nuevo cierre;
- cambio de provincia, localidad, motivo, vía, sentido o kilómetros;
- reapertura parcial;
- reapertura total;
- cierre posterior de una carretera que ya había sido reabierta.

El aviso no añade etiquetas delante de la vía, el sentido ni los kilómetros.
La ubicación, el sentido, el tramo y la fecha de publicación aparecen en
cursiva; la causa y la vía, en negrita. Por ejemplo:

> **🔴 CARRETERA CORTADA**
>
> *📍 Granada — Güéjar Sierra*  
> **Desprendimiento**
>
> **A-395**
>
> *Doble sentido*  
> *31,000–39,000*  
> *Publicado: 17/08/2026 · 13:45 h*

## Configuración de Telegram

El bot debe ser administrador del canal y tener permiso para publicar mensajes.
En `Settings → Secrets and variables → Actions` del repositorio deben crearse
dos **Repository secrets**:

- `TELEGRAM_BOT_TOKEN`: token entregado por BotFather.
- `TELEGRAM_CHAT_ID`: `@usuario_del_canal` si el canal es público, o el
  identificador numérico `-100…` si es privado.

El token nunca debe guardarse en un archivo, commit, variable normal ni mensaje
de soporte.

## Puesta en marcha

1. Añadir los dos secretos de Telegram.
2. Ejecutar manualmente `Prueba de Telegram` desde la pestaña **Actions**.
3. Confirmar que el canal recibe el mensaje de prueba.
4. Ejecutar manualmente `Vigilancia de carreteras cortadas - Andalucía`.
5. Verificar la lista inicial de cortes publicada en el canal.

Cada ejecución consulta DGT seis veces, con quince minutos entre comprobaciones,
y después lanza automáticamente su relevo. El estado operativo se conserva en
la rama `estado`, separada del código de `main`.

## Desarrollo local

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

