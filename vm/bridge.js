const rhea = require('rhea');
const { Client, Message } = require('azure-iot-device');
const { Amqp } = require('azure-iot-device-amqp');
const { Http: ProvHttp } = require('azure-iot-provisioning-device-http');
const { ProvisioningDeviceClient } = require('azure-iot-provisioning-device');
const { SymmetricKeySecurityClient } = require('azure-iot-security-symmetric-key');

const { ID_SCOPE, DEVICE_ID, DEVICE_KEY, BROKER_PASS } = process.env;
const BROKER_HOST = process.env.BROKER_HOST || '127.0.0.1'; // 127.0.0.1 evita problemas con IPv6
const BROKER_PORT = Number(process.env.BROKER_PORT || 5672);

if (!ID_SCOPE || !DEVICE_ID || !DEVICE_KEY || !BROKER_PASS) {
  console.error('Faltan variables: ID_SCOPE, DEVICE_ID, DEVICE_KEY, BROKER_PASS');
  process.exit(1);
}
const log = (...a) => console.log(new Date().toISOString(), ...a);

// 1) Provisioning (DPS) por AMQP -> hub asignado
log('Iniciando registro en DPS (HTTP)...');
const watchdog = setTimeout(() => {
  console.error('DPS no respondió en 60 s. Revisa ID_SCOPE, DEVICE_ID y DEVICE_KEY.');
  process.exit(1);
}, 60000);

const prov = ProvisioningDeviceClient.create(
  'global.azure-devices-provisioning.net', ID_SCOPE,
  new ProvHttp(), new SymmetricKeySecurityClient(DEVICE_ID, DEVICE_KEY));

prov.register((err, res) => {
  clearTimeout(watchdog);
  if (err) { console.error('DPS error:', err); process.exit(1); }
  log('DPS OK, hub asignado:', res.assignedHub);
  // ... el resto igual (cs, Client.fromConnectionString(cs, Amqp), etc.)

  // 2) Conexión AMQP al hub (puerto 5671, TLS)
  const cs = `HostName=${res.assignedHub};DeviceId=${res.deviceId};SharedAccessKey=${DEVICE_KEY}`;
  const hub = Client.fromConnectionString(cs, Amqp);
  hub.on('error', e => console.error('hub error:', e.message));
  hub.on('disconnect', () => log('hub desconectado'));
  hub.open(e => {
    if (e) { console.error('No se pudo abrir AMQP:', e.message); process.exit(1); }
    log('AMQP abierto con', res.assignedHub);
    listen(hub);
  });
});

// 3) Consumir de la cola local y reenviar
function listen(hub) {
  const container = rhea.create_container();
  const conn = container.connect({
    host: BROKER_HOST, port: BROKER_PORT,
    username: 'lab4', password: BROKER_PASS,
    reconnect: true,
  });

  container.on('connection_open', () => log(`conectado al broker ${BROKER_HOST}:${BROKER_PORT}`));
  container.on('disconnected', () => log('broker desconectado, reintentando...'));

  conn.open_receiver({
    source: { address: 'telemetria', capabilities: ['queue'] }, // ANYCAST
    autoaccept: false,   // confirmamos nosotros, solo si Central aceptó
    credit_window: 10,   // control de flujo: máximo 10 mensajes en vuelo
  });

  container.on('message', ctx => {
    const b = ctx.message.body;
    const text = Buffer.isBuffer(b) ? b.toString('utf8')
      : typeof b === 'string' ? b : JSON.stringify(b);
    log('<- broker', text);

    const m = new Message(text);
    m.contentType = 'application/json';
    m.contentEncoding = 'utf-8';
    hub.sendEvent(m, e => {
      if (e) {
        console.error('-> Central ERROR:', e.message);
        ctx.delivery.release();   // el mensaje vuelve a la cola
      } else {
        log('-> Central (AMQP) OK');
        ctx.delivery.accept();    // ahora sí se borra de la cola
      }
    });
  });
}