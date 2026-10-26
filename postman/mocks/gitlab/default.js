// Local synthetic GitLab subset. Never connect this handler to a live project.
// Request interception is enabled so POST bodies and query parameters are visible.
// Keep this handler self-contained so the same code can run in a local mock
// and a deployed Postman mock. Parallel JSON fixtures live under fixtures/gitlab.
const project = {id: 731, name: 'my-project', path_with_namespace: 'example-group/my-project', default_branch: 'main', web_url: 'https://gitlab.example.com/example-group/my-project'};
const seedBranches = [
  {name: 'main', commit: {id: 'a1b2c3d4e5f6'}, protected: true, default: true, can_push: false},
  {name: 'al/run-42-delivery', commit: {id: 'd4e5f6a1b2c3'}, protected: false, default: false, can_push: true}
];
const seedMrs = [{id: 91001, iid: 14, project_id: 731, title: 'Synthetic delivery fixture', state: 'opened', source_branch: 'al/run-42-delivery', target_branch: 'main', sha: 'd4e5f6a1b2c3', web_url: 'https://gitlab.example.com/example-group/my-project/-/merge_requests/14', merge_status: 'can_be_merged', detailed_merge_status: 'mergeable'}];
const pipelines = [
  {id: 5801, iid: 92, project_id: 731, sha: 'd4e5f6a1b2c3', ref: 'refs/merge-requests/14/head', status: 'success', source: 'merge_request_event', web_url: 'https://gitlab.example.com/example-group/my-project/-/pipelines/5801'},
  {id: 5798, iid: 91, project_id: 731, sha: 'c3d4e5f6a1b2', ref: 'refs/merge-requests/14/head', status: 'failed', source: 'merge_request_event', web_url: 'https://gitlab.example.com/example-group/my-project/-/pipelines/5798'}
];
const jobs = [
  {id: 82001, name: 'unit-tests', status: 'success', stage: 'test', pipeline: {id: 5801}},
  {id: 82002, name: 'integration-tests', status: 'success', stage: 'test', pipeline: {id: 5801}},
  {id: 81981, name: 'unit-tests', status: 'failed', stage: 'test', pipeline: {id: 5798}}
];
const testReport = {total_time: 18.4, total_count: 42, success_count: 41, failed_count: 1, skipped_count: 0, test_suites: [{name: 'synthetic-suite', total_count: 42, success_count: 41, failed_count: 1, skipped_count: 0}]};
const environments = [{id: 310, name: 'staging', slug: 'staging', external_url: 'https://staging.example.invalid', state: 'available', tier: 'staging'}, {id: 311, name: 'production', slug: 'production', state: 'available', tier: 'production'}];
const deployments = [{id: 4102, iid: 3, ref: 'al/run-42-delivery', sha: 'd4e5f6a1b2c3', status: 'success', environment: {id: 310, name: 'staging'}}, {id: 4098, iid: 2, ref: 'main', sha: 'c3d4e5f6a1b2', status: 'success', environment: {id: 310, name: 'staging'}}];

async function state() {
  const store = (await pm.state.get('gitlab_data')) || {};
  if (!store.gitlab_branches) store.gitlab_branches = seedBranches.slice();
  if (!store.gitlab_merge_requests) store.gitlab_merge_requests = seedMrs.slice();
  if (!store.gitlab_next_mr_iid) store.gitlab_next_mr_iid = 15;
  if (!store.gitlab_next_branch_id) store.gitlab_next_branch_id = 1;
  return store;
}
function send(res, code, value, headers = {}) {
  res.writeHead(code, {'content-type': 'application/json', ...headers});
  res.end(JSON.stringify(value));
}
function pathParts(req) {
  return new URL(req.url, 'http://mock.local').pathname.split('/').filter(Boolean);
}
function projectMatch(parts) {
  return parts[0] === 'api' && parts[1] === 'v4' && parts[2] === 'projects' && decodeURIComponent(parts[3] || '') === 'example-group/my-project';
}
function authOk(req) {
  return Boolean(req.headers && (req.headers['private-token'] || /^Bearer\s+\S+/i.test(req.headers.authorization || '')));
}
function body(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  try { return JSON.parse(req.rawBody || '{}'); } catch (_) { return {}; }
}
function exactBranch(store, name) {
  return store.gitlab_branches.find((item) => item.name === name);
}

const http = require('http');
const server = http.createServer(async function handler(req, res) {
  const parts = pathParts(req);
  const method = (req.method || 'GET').toUpperCase();
  const query = req.query || Object.fromEntries(new URL(req.url, 'http://mock.local').searchParams.entries());
  if (method === 'POST' && parts.join('/') === '__test/reset') {
    await pm.state.set('gitlab_data', {});
    return send(res, 200, {reset: true});
  }
  if (method !== 'GET' && method !== 'HEAD' && method !== 'POST') return send(res, 405, {message: 'Method Not Allowed'});
  if (!authOk(req)) return send(res, 401, {message: '401 Unauthorized'});
  if (!projectMatch(parts)) return send(res, 404, {message: '404 Project Not Found'});
  const store = await state();
  if (parts.length === 4 && method === 'GET') return send(res, 200, project, {'x-next-page': ''});
  if (parts[4] === 'repository' && parts[5] === 'branches' && parts.length === 7 && method === 'GET') {
    const item = exactBranch(store, decodeURIComponent(parts[6]));
    return item ? send(res, 200, item) : send(res, 404, {message: '404 Branch Not Found'});
  }
  if (parts[4] === 'repository' && parts[5] === 'branches' && parts.length === 6 && method === 'POST') {
    const data = body(req);
    if (!data.branch || !data.ref) return send(res, 400, {message: 'branch and ref are required'});
    if (exactBranch(store, data.branch)) return send(res, 409, {message: 'Branch already exists'});
    const source = exactBranch(store, data.ref);
    if (!source && !/^[0-9a-f]{7,40}$/i.test(data.ref)) return send(res, 400, {message: 'ref not found'});
    const branch = {name: data.branch, commit: source ? source.commit : {id: data.ref, short_id: data.ref.slice(0, 8)}, protected: false, default: false, can_push: true, web_url: `${project.web_url}/-/tree/${encodeURIComponent(data.branch)}`};
    store.gitlab_branches.push(branch);
    await pm.state.set('gitlab_data', store);
    return send(res, 201, branch);
  }
  if (parts[4] === 'merge_requests' && parts.length === 5 && method === 'GET') {
    const rows = store.gitlab_merge_requests.filter((mr) => (!query.source_branch || mr.source_branch === query.source_branch) && (!query.target_branch || mr.target_branch === query.target_branch) && (!query.state || mr.state === query.state));
    const page = Math.max(1, Number(query.page || 1));
    const perPage = Math.min(100, Math.max(1, Number(query.per_page || 20)));
    const start = (page - 1) * perPage;
    return send(res, 200, rows.slice(start, start + perPage), {'x-next-page': start + perPage < rows.length ? String(page + 1) : ''});
  }
  if (parts[4] === 'merge_requests' && parts.length === 5 && method === 'POST') {
    const data = body(req);
    if (!data.source_branch || !data.target_branch || !data.title) return send(res, 400, {message: 'source_branch, target_branch and title are required'});
    if (!exactBranch(store, data.source_branch) || !exactBranch(store, data.target_branch)) return send(res, 400, {message: 'source or target branch not found'});
    const duplicate = store.gitlab_merge_requests.find((mr) => mr.state === 'opened' && mr.source_branch === data.source_branch && mr.target_branch === data.target_branch);
    if (duplicate) return send(res, 409, {message: 'An open merge request already exists for these branches', existing_iid: duplicate.iid});
    const iid = store.gitlab_next_mr_iid++;
    const mr = {id: 91000 + iid, iid, project_id: project.id, title: data.title, description: data.description || '', state: 'opened', source_branch: data.source_branch, target_branch: data.target_branch, sha: exactBranch(store, data.source_branch).commit.id, web_url: `${project.web_url}/-/merge_requests/${iid}`, merge_status: 'checking', detailed_merge_status: 'checking'};
    store.gitlab_merge_requests.push(mr);
    await pm.state.set('gitlab_data', store);
    return send(res, 201, mr);
  }
  if (parts[4] === 'merge_requests' && parts[5]) {
    const iid = Number(parts[5]);
    const mr = store.gitlab_merge_requests.find((item) => item.iid === iid);
    if (!mr) return send(res, 404, {message: '404 Merge Request Not Found'});
    if (parts[6] === 'pipelines' && method === 'GET') return send(res, 200, pipelines.filter((item) => item.ref.endsWith(`/${iid}/head`)));
    if (parts.length === 6 && method === 'GET') return send(res, 200, mr);
  }
  if (parts[4] === 'pipelines' && parts[5]) {
    const pipelineId = Number(parts[5]);
    const pipeline = pipelines.find((item) => item.id === pipelineId);
    if (!pipeline) return send(res, 404, {message: '404 Pipeline Not Found'});
    if (parts[6] === 'jobs' && method === 'GET') {
      const rows = jobs.filter((item) => item.pipeline.id === pipelineId);
      const page = Math.max(1, Number(query.page || 1));
      const perPage = Math.min(100, Math.max(1, Number(query.per_page || 20)));
      return send(res, 200, rows.slice((page - 1) * perPage, page * perPage), {'x-next-page': ''});
    }
    if (parts[6] === 'test_report' && method === 'GET') return send(res, 200, testReport);
    if (parts.length === 6 && method === 'GET') return send(res, 200, pipeline);
  }
  if (parts[4] === 'environments' && method === 'GET') {
    if (parts[5]) {
      const environment = environments.find(e => e.id === Number(parts[5]));
      if (!environment) return send(res, 404, {message: 'Environment not found'});
      return send(res, 200, {...environment, last_deployment: deployments.find(d => d.environment.id === environment.id) || null});
    }
    return send(res, 200, environments, {'x-next-page': ''});
  }
  if (parts[4] === 'deployments' && method === 'GET') {
    const rows = deployments.filter((item) => !query.environment || item.environment.name === query.environment);
    return send(res, 200, rows, {'x-next-page': ''});
  }
  return send(res, 501, {message: `501 Not Implemented: ${method} /${parts.join('/')}`});
});
server.listen(process.env.PORT || 8787);
