// SPDX-License-Identifier: Apache-2.0
// Execute the actual Function-node bodies from the importable example.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

process.env.TZ = 'Europe/Berlin';
const nodes = JSON.parse(fs.readFileSync(path.join(__dirname, '../docs/_static/nodered-price-preflight.json'), 'utf8'));
const byId = new Map(nodes.map(node => [node.id, node]));

function harness(timestamp = '2026-10-10T22:49:00+02:00') {
    let now = Date.parse(timestamp);
    const store = new Map();
    const errors = [];
    const flow = {get: key => store.get(key), set: (key, value) => store.set(key, value)};
    const sandbox = {
        flow,
        node: {error: message => errors.push(message), status() {}},
        env: {get: key => key === 'EOS_BASE_URL' ? 'http://eos.example:8505/' : undefined},
        Date: class extends Date {
            constructor(...args) {super(...(args.length ? args : [now]));}
            static now() {return now;}
        },
    };
    function call(id, msg) {
        sandbox.msg = msg;
        const result = vm.runInNewContext(`(function() {${byId.get(id).func}\n})()`, sandbox);
        return result === undefined ? undefined : JSON.parse(JSON.stringify(result));
    }
    function response(topic = 'rc_optimize') {
        const request = call('rc_price_raw_url', {topic, payload: {soc: {battery: 50}}});
        const values = {};
        for (let time = request.rcPriceWindow.start; time < request.rcPriceWindow.end; time += 900000) {
            values[new Date(time).toISOString()] = time % 2700000 === 0 ? 0 : -0.0001;
        }
        return {...request, statusCode: 200, payload: {data: values, tz: 'Europe/Berlin', dtype: 'float64'}};
    }
    return {call, response, flow, errors, sandbox, advance: ms => {now += ms;}};
}

test('optimization trigger is only downstream of the completed price check', () => {
    assert.deepEqual(byId.get('rc_optimize_tick').wires, [['rc_price_raw_url']]);
    assert.equal(byId.get('rc_optimize_tick').topic, 'rc_optimize');
    assert.equal(byId.get('rc_optimize_tick').once, false);
    assert.equal(byId.get('rc_optimize_tick').repeat, '');
    assert.deepEqual(byId.get('rc_price_raw_http').wires, [['rc_price_raw_check']]);
    assert.deepEqual(byId.get('rc_price_raw_check').wires, [['rc_prediction_recovery_http'], ['rc_optimize_ready']]);
    assert.equal(byId.get('rc_price_raw_check').outputs, 2);
    assert(!nodes.some(node => /mqtt|home-connect|mysql/.test(node.type)));
    for (const node of nodes.filter(node => node.type === 'function')) new vm.Script(`(function() {${node.func}\n})`);
});

for (const [timestamp, expected] of [
    ['2026-10-10T22:49:00+02:00', 96],
    ['2026-03-29T03:01:00+02:00', 92],
    ['2026-10-25T02:01:00+01:00', 100],
]) {
    test(`complete raw prices include negative/zero values and ${expected} DST-aware slots`, () => {
        const h = harness(timestamp);
        h.flow.set('rcPriceCoverage', {complete: false});
        const msg = h.response();
        assert.equal(Object.keys(msg.payload.data).length, expected);
        assert(msg.url.includes('/v1/prediction/series?'));
        assert(msg.url.includes('processing=raw'));
        assert.equal(msg.method, 'GET');
        const result = h.call('rc_price_raw_check', msg);
        assert.equal(h.flow.get('rcPriceCoverage').complete, true);
        assert.equal(h.flow.get('rcPriceCoverage').expected_slots, expected);
        assert.deepEqual(result, [null, {topic: 'rc_optimize'}]);
        assert.equal(result[1].payload, undefined);
        assert.equal(result[1].statusCode, undefined);
        assert.equal(h.errors.length, 0);
    });
}

test('a background refresh records coverage without starting optimization', () => {
    const h = harness();
    assert.equal(h.call('rc_price_raw_check', h.response('refresh')), null);
    assert.equal(h.flow.get('rcPriceCoverage').complete, true);
});

for (const missing of ['null', 'absent', 'truncated', 'shifted']) {
    test(`${missing} current/future prices stop optimization without treating a real gap as a cold start`, () => {
        const h = harness();
        const msg = h.response();
        const keys = Object.keys(msg.payload.data);
        if (missing === 'null') msg.payload.data[keys.at(-1)] = null;
        if (missing === 'absent') delete msg.payload.data[keys.at(-1)];
        if (missing === 'truncated') {
            for (const key of keys.slice(-3)) delete msg.payload.data[key];
        }
        if (missing === 'shifted') {
            msg.payload.data[new Date(Date.parse(keys.at(-1)) + 60000).toISOString()] = msg.payload.data[keys.at(-1)];
            delete msg.payload.data[keys.at(-1)];
        }
        h.flow.set('rcPriceCoverage', {complete: true});
        assert.equal(h.call('rc_price_raw_check', msg), null);
        assert.equal(h.flow.get('rcPriceCoverage').complete, false);
        assert.equal(h.flow.get('rcPredictionRecovery'), undefined);
    });
}

test('missing historical slots do not invalidate a complete remaining day', () => {
    const h = harness();
    const msg = h.response();
    delete msg.payload.data[Object.keys(msg.payload.data)[0]];
    assert.deepEqual(h.call('rc_price_raw_check', msg), [null, {topic: 'rc_optimize'}]);
});

for (const payload of [[], {data: []}, {data: {'invalid timestamp': 1}}, {data: {'2026-10-10T23:45:00+02:00': '0.1'}}]) {
    test(`malformed raw response ${JSON.stringify(payload)} cannot trigger optimization or recovery`, () => {
        const h = harness();
        const msg = {...h.response(), payload};
        assert.equal(h.call('rc_price_raw_check', msg), null);
        assert.equal(h.flow.get('rcPriceCoverage').complete, false);
        assert.equal(h.flow.get('rcPredictionRecovery'), undefined);
    });
}

test('HTTP failures cannot use otherwise valid prices or initiate recovery', () => {
    const h = harness();
    for (const payload of [h.response().payload, {data: {}}]) {
        assert.equal(h.call('rc_price_raw_check', {...h.response(), statusCode: 500, payload}), null);
        assert.equal(h.flow.get('rcPredictionRecovery'), undefined);
    }
});

test('a response arriving after midnight cannot release the previous day check', () => {
    const h = harness('2026-10-10T23:59:59+02:00');
    const msg = h.response();
    h.advance(2000);
    assert.equal(h.call('rc_price_raw_check', msg), null);
    assert.equal(h.flow.get('rcPriceCoverage').complete, false);
    assert.equal(h.flow.get('rcPredictionRecovery'), undefined);
});

for (const values of [{}, {'2026-10-10T23:45:00+02:00': null}]) {
    test(`empty cold-start prices ${JSON.stringify(values)} update predictions with a five-minute backoff`, () => {
        const h = harness();
        const msg = {...h.response(), payload: {data: values}};
        const result = h.call('rc_price_raw_check', msg);
        assert.equal(result[1], null);
        assert.equal(result[0].url, 'http://eos.example:8505/v1/prediction/update');
        assert.equal(result[0].method, 'POST');
        assert.equal(h.flow.get('rcPriceCoverage').complete, false);
        assert.equal(h.call('rc_price_raw_check', msg), null);
        h.advance(300001);
        assert(h.call('rc_price_raw_check', msg)[0]);
    });
}

test('busy optimization suppresses cold-start recovery', () => {
    const h = harness();
    h.flow.set('rcBusy', true);
    assert.equal(h.call('rc_price_raw_check', {...h.response(), payload: {data: {}}}), null);
    assert.equal(h.flow.get('rcPredictionRecovery'), undefined);
});

test('successful recovery refreshes forecasts and waits for the next optimization request', () => {
    const h = harness();
    assert.deepEqual(h.call('rc_prediction_recovery_done', {statusCode: 200}), {topic: 'refresh'});
    assert.equal(h.flow.get('rcPredictionRecovery').status, 'loaded');
    assert.deepEqual(byId.get('rc_prediction_recovery_done').wires, [['rc_refresh_forecasts']]);
    assert.equal(h.call('rc_prediction_recovery_done', {statusCode: 500}), null);
    assert.equal(h.flow.get('rcPredictionRecovery').status, 'failed');
});

test('missing EOS URL blocks HTTP requests and recovery', () => {
    const h = harness();
    const msg = {...h.response(), payload: {data: {}}};
    h.sandbox.env.get = () => '';
    assert.equal(h.call('rc_price_raw_url', {}), null);
    assert.equal(h.call('rc_price_raw_check', msg), null);
    assert.equal(h.flow.get('rcPredictionRecovery'), undefined);
});

if (process.env.EOS_PRICE_TEST_SERIES) {
    test('accepts the real EOS PydanticDateTimeSeries response contract', () => {
        const h = harness();
        const payload = JSON.parse(fs.readFileSync(process.env.EOS_PRICE_TEST_SERIES, 'utf8'));
        const msg = {...h.response(), payload};
        assert.deepEqual(h.call('rc_price_raw_check', msg), [null, {topic: 'rc_optimize'}]);
        assert.equal(h.flow.get('rcPriceCoverage').complete, true);
    });
}
