const rhea = require('rhea');

const HOST = process.env.BROKER_HOST || 'localhost';
const PORT = Number(process.env.BROKER_PORT || 5672);
const INTERVAL_MS = Number(process.env.INTERVAL_MS || 5000);

const container = rhea.create_container();

const connection = container.connect({
  host: HOST,
  port: PORT,
  username: 'lab4',
  password: process.env.BROKER_PASS,
  reconnect: true,          // si el broker cae, reintenta solo
});

// Artemis crea la dirección/cola "telemetria" al primer attach
const sender = connection.open_sender({
  target: { address: 'telemetria', capabilities: ['queue'] },  // 'queue' = ANYCAST
});

container.on('connection_open', () =>
  console.log(`[${new Date().toISOString()}] conectado al broker ${HOST}:${PORT}`));
container.on('disconnected', () =>
  console.log(`[${new Date().toISOString()}] desconectado, reintentando...`));
container.on('connection_error', ctx =>
  console.error('error de conexión:', ctx.connection.get_error()));

// El broker confirma que recibió y aceptó el mensaje
container.on('accepted', ctx =>
  console.log(`   ack del broker (delivery #${ctx.delivery.id})`));
container.on('rejected', ctx => console.error('   mensaje RECHAZADO'));
container.on('released', ctx => console.error('   mensaje liberado (reintentar)'));

function lectura() {
  return {
    HeartRate: Math.floor(60 + Math.random() * 51),            // 60-110
    SPO2: +(92 + Math.random() * 8).toFixed(1),                          // 92-100
    Temperature: +(36 + Math.random() * 2.5).toFixed(1),                 // 36-38.5
  };
}

setInterval(() => {
  if (!sender.sendable()) {
    console.log('sin crédito o sin conexión, se omite este envío');
    return;
  }
  const data = lectura();
  sender.send({
    durable: true,                       // el broker lo guarda en disco
    content_type: 'application/json',
    body: JSON.stringify(data),
  });
  console.log(`[${new Date().toISOString()}] publicado:`, data);
}, INTERVAL_MS);
