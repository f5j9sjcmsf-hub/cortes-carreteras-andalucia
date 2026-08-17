# Carreteras cortadas - Andalucía

Bot independiente de Telegram que vigila todos los cortes completos de
carreteras publicados por la Dirección General de Tráfico en Andalucía, sin
limitarse a una causa concreta.

Este proyecto no comparte bot, canal, repositorio, secretos ni estado con el
bot de incendios forestales. El funcionamiento del bot anterior no se modifica.

## Fuente y alcance

La fuente oficial es la publicación de incidencias
[DATEX II v3.7 de la DGT](https://nap.dgt.es/en/dataset/incidencias-dgt-datex2-v3-7).
El bot incluye únicamente registros activos con cierre completo de la carretera
o de un sentido. Se excluyen retenciones, carriles aislados cerrados,
circulación alterna y restricciones que no supongan un corte total.

Se incluyen todas las causas publicadas por DGT: obras, desprendimientos,
daños en la vía, meteorología, inundaciones, nieve, accidentes, obstáculos y
cualquier categoría futura que acompañe a un cierre completo.

## Sentidos y agrupación

- `negative` en el perfil español de DGT significa sentido kilométrico
  creciente.
- `positive` significa sentido kilométrico decreciente.
- `both` significa doble sentido.
- Dos registros opuestos con la misma vía, tramo, provincia, localidades y
  causa se presentan como un único corte de doble sentido.

La agrupación conserva todos los identificadores DGT originales. Si desaparece
solo uno de los dos sentidos, se publica una reapertura parcial; si desaparecen
ambos, se publica la reapertura total.

## Avisos

La primera ejecución publica la lista completa de cortes activos. Después solo
se envían mensajes cuando ocurre alguno de estos cambios:

- nuevo cierre;
- cambio de provincia, localidad, motivo, vía, sentido o kilómetros;
- reapertura parcial;
- reapertura total;
- cierre posterior de una carretera que ya había sido reabierta.

El formato general es:

```text
🔴 CARRETERA CORTADA

📍 Granada — Güéjar Sierra
Desprendimiento

Vía: A-395
Sentido: Doble sentido
Kilómetros: 31–39
```

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

