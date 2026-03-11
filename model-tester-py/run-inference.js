const fs = require('fs');
const Module = require('./sotercare-final-model-wasm-v1/node/edge-impulse-standalone');

// In modern edge impulse standing node exports, Module is initialized when require completes.
// Or we can just wait 100ms.
setTimeout(() => {
    try {
        let ret = Module.init();
        // ret might be 0
        let props = Module.get_properties();
        
        if (process.argv[2] === '--info') {
            console.log(JSON.stringify({
                input_features_count: props.input_features_count,
                interval_ms: props.frequency ? (1000 / props.frequency) : null
            }));
            process.exit(0);
        }

        if (!process.argv[2]) {
            console.error(JSON.stringify({ error: "No features provided" }));
            process.exit(1);
        }

        let features = process.argv[2].trim().split(',').map(Number);
        if (features.length !== props.input_features_count) {
            console.error(JSON.stringify({ 
                error: `Invalid feature count. Expected ${props.input_features_count}, got ${features.length}`
            }));
            process.exit(1);
        }

        // Run
        let typedArray = new Float32Array(features);
        let numBytes = typedArray.length * typedArray.BYTES_PER_ELEMENT;
        let ptr = Module._malloc(numBytes);
        let heapBytes = new Uint8Array(Module.HEAPU8.buffer, ptr, numBytes);
        heapBytes.set(new Uint8Array(typedArray.buffer));

        let res = Module.run_classifier(ptr, features.length, false);
        Module._free(ptr);

        if (res.result !== 0) {
            throw new Error('Classification failed (err code: ' + res.result + ')');
        }

        // Parse result
        let jsResult = { anomaly: res.anomaly, results: [] };
        for (let cx = 0; cx < res.size(); cx++) {
            let c = res.get(cx);
            jsResult.results.push({ label: c.label, value: c.value });
            c.delete();
        }
        res.delete();

        console.log(JSON.stringify(jsResult));
        process.exit(0);
    } catch (err) {
        console.error(JSON.stringify({ error: err.message || err }));
        process.exit(1);
    }
}, 200);
