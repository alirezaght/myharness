const http = require('http');
http.createServer((_req, res) => {
  res.writeHead(429, {'content-type': 'application/json', 'retry-after': '30'});
  res.end(JSON.stringify({message: '429 Too Many Requests'}));
}).listen(process.env.PORT || 8787);
