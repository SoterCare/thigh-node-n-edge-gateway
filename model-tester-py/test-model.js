const Module = require('./sotercare-final-model-wasm-v1/node/edge-impulse-standalone');
let classifierInitialized = false;
Module.onRuntimeInitialized = function() {
    classifierInitialized = true;
    let ret = Module.init();
    if (typeof ret === 'number' && ret != 0) {
        console.error('init failed', ret);
    }
    console.log("Model initialized");
    console.log(Module.get_properties());
};
console.log("Waiting for initialization...");
