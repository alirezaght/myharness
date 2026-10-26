// Synthetic GitHub REST service; no live provider traffic or real tokens.
const http = require('http');
const sha = 'a'.repeat(40);
const base = 'b'.repeat(40);
function send(res, code, value) {
  res.writeHead(code, {'content-type': 'application/json'});res.end(JSON.stringify(value));
}
function pull(number, branch, commit, body = '') {
  return {number, state: 'open', head: {ref: branch, sha: commit}, base: {ref: 'main'},
    body, html_url: `https://github.example/example/harness/pull/${number}`};
}
http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://mock.local');
  const parts = url.pathname.split('/').filter(Boolean);
  const method = req.method || 'GET';
  const body = req.body || {};
  let store = await pm.state.get('github_data') || {branches: {main: base, candidate: sha},
    pulls: [pull(14, 'candidate', sha)], next: 15, mode: {}};
  if (method === 'POST' && url.pathname === '/__test/reset') {
    await pm.state.set('github_data', null);return send(res, 200, {reset: true});
  }
  if (method === 'POST' && url.pathname === '/__test/mode') {
    store.mode = body;await pm.state.set('github_data', store);return send(res, 200, body);
  }
  if (!/^Bearer\s+\S+/.test(req.headers.authorization || '')) return send(res, 401, {});
  if (parts.slice(0,5).join('/') !== 'api/v3/repos/example/harness') return send(res, 404, {});
  const path = '/' + parts.slice(5).join('/');
  if (path === '/' && method === 'GET') return send(res, 200, {id: 731, full_name: 'example/harness'});
  if (path.startsWith('/branches/') && method === 'GET') {
    const branch = decodeURIComponent(parts.slice(6).join('/'));
    return store.branches[branch] ? send(res, 200, {name: branch, commit: {sha: store.branches[branch]}}) : send(res, 404, {});
  }
  if (path.startsWith('/commits/') && !path.endsWith('/check-runs')) return send(res, 200, {sha: parts[6]});
  if (path === '/git/refs' && method === 'POST') {
    const branch = String(body.ref || '').replace(/^refs\/heads\//, '');
    if (!branch || store.branches[branch]) return send(res, 422, {message: 'Reference already exists'});
    store.branches[branch] = body.sha;await pm.state.set('github_data', store);
    return send(res, 201, {ref: body.ref, object: {sha: body.sha}});
  }
  if (path === '/pulls' && method === 'POST') {
    if (store.pulls.some(p => p.head.ref === body.head && p.base.ref === body.base)) return send(res, 422, {message: 'Pull request exists'});
    const value = pull(store.next++, body.head, store.branches[body.head], body.body);
    store.pulls.push(value);await pm.state.set('github_data', store);return send(res, 201, value);
  }
  if (path === '/pulls') {
    const head = (url.searchParams.get('head') || '').replace(/^example:/, '');
    return send(res, 200, store.pulls.filter(p => !head || p.head.ref === head));
  }
  if (/^\/pulls\/\d+$/.test(path)) return send(res, 200, store.pulls.find(p => p.number === Number(parts[6])) || {});
  if (path === '/actions/runs') {
    const runs = [
      {id: 100, workflow_id: 1, head_sha: sha, status: 'completed', conclusion: 'failure', run_attempt: 1},
      {id: 101, workflow_id: 1, head_sha: sha, status: 'completed', conclusion: 'success', run_attempt: 1},
      {id: 102, workflow_id: 2, head_sha: sha, status: 'completed', conclusion: store.mode.failure ? 'failure' : 'success', run_attempt: store.mode.attempt || 1}
    ];
    return send(res, 200, {workflow_runs: runs.filter(r => !url.searchParams.get('head_sha') || r.head_sha === url.searchParams.get('head_sha'))});
  }
  if (/^\/actions\/runs\/\d+\/attempts\/\d+\/jobs$/.test(path)) return send(res, 200, {jobs: [{id: Number(parts[7])*10, name: 'unit-tests', status: 'completed', conclusion: store.mode.failure && parts[7] === '102' ? 'failure' : 'success', html_url: 'https://github.example/actions/job'}]});
  if (path.endsWith('/check-runs')) return send(res, 200, {check_runs: [{id: 91, name: 'test reporter', conclusion: store.mode.failure ? 'failure' : 'success', output: {summary: 'Repository test summary'}}]});
  if (path === '/environments') return send(res, 200, {environments: [{id: 3, name: 'staging'}]});
  if (path === '/environments/staging') return send(res, 200, {id: 3, name: 'staging'});
  if (path === '/deployments') return send(res, 200, [{id: 7, environment: 'staging', sha: store.mode.deployment_sha || sha}]);
  if (path === '/deployments/7/statuses') return send(res, 200, [{id: 8, state: store.mode.deployment_status || 'success', environment_url: 'https://staging.example.invalid'}]);
  return send(res, 501, {message: 'Unmodeled GitHub request', path});
}).listen(process.env.PORT || 8788);
