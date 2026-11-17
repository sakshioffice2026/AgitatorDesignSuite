// CAD-style STEP viewer.
// Parses the server-generated STEP file in the browser with occt-import-js
// (OpenCascade WASM) and renders it with three.js (WebGL).
// No animation loop: the scene is redrawn only when the user interacts.
// Expects: <div id="viewer-container" data-step-url="..."></div>
// Requires (loaded before this file): three r128, OrbitControls r128, occt-import-js.
(function () {
    'use strict';

    var container = document.getElementById('viewer-container');
    if (!container) return;

    var stepUrl = container.getAttribute('data-step-url');
    var wasmBase = 'https://cdn.jsdelivr.net/npm/occt-import-js@0.0.23/dist/';

    container.style.position = 'relative';
    container.style.overflow = 'hidden';

    var width = container.clientWidth;
    var height = container.clientHeight;

    // ---------- scene ----------
    var scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf7f7f7);

    var camera = new THREE.PerspectiveCamera(30, width / height, 0.1, 1000000);
    scene.add(camera);

    var renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(width, height);
    container.appendChild(renderer.domElement);

    // Headlight attached to the camera: shading stays constant while orbiting.
    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    var headLight = new THREE.DirectionalLight(0xffffff, 0.7);
    headLight.position.set(0.5, 1, 1);
    camera.add(headLight);

    var controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;

    var parts = [];            // { name, mesh, edges, material, edgeMaterial, isTank, visible }
    var sceneRadius = 1;
    var wireframeMode = false;
    var tankTransparent = true;

    // ---------- render on demand ----------
    var renderQueued = false;
    function render() {
        if (renderQueued) return;
        renderQueued = true;
        requestAnimationFrame(function () {
            renderQueued = false;
            renderer.render(scene, camera);
        });
    }
    controls.addEventListener('change', render);

    // ---------- helpers ----------
    function showError(message) {
        container.innerHTML = '<p class="text-danger p-3 mb-0">' + message + '</p>';
    }

    function collectNames(node, names) {
        if (!node) return;
        if (node.meshes && node.name) {
            node.meshes.forEach(function (i) { if (!names[i]) names[i] = node.name; });
        }
        (node.children || []).forEach(function (c) { collectNames(c, names); });
    }

    function buildGeometry(m) {
        var geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(m.attributes.position.array, 3));
        if (m.attributes.normal) {
            geometry.setAttribute('normal', new THREE.Float32BufferAttribute(m.attributes.normal.array, 3));
        }
        geometry.setIndex(new THREE.Uint32BufferAttribute(m.index.array, 1));
        if (!m.attributes.normal) geometry.computeVertexNormals();
        geometry.computeBoundingSphere();
        return geometry;
    }

    function applyPartState(p) {
        p.mesh.visible = p.visible && !wireframeMode;
        p.edges.visible = p.visible;
    }

    function applyTankTransparency() {
        parts.forEach(function (p) {
            if (!p.isTank) return;
            p.material.transparent = tankTransparent;
            p.material.opacity = tankTransparent ? 0.15 : 1.0;
            p.material.depthWrite = !tankTransparent;
            p.material.needsUpdate = true;
            p.mesh.renderOrder = tankTransparent ? 10 : 0;
            p.edgeMaterial.transparent = tankTransparent;
            p.edgeMaterial.opacity = tankTransparent ? 0.6 : 1.0;
            p.edgeMaterial.needsUpdate = true;
            p.edges.renderOrder = tankTransparent ? 11 : 1;
        });
    }

    // ---------- camera views ----------
    function setView(dirVec) {
        var dir = dirVec.clone().normalize();
        var dist = sceneRadius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.1;

        camera.up.set(0, 1, 0);
        camera.position.copy(controls.target).add(dir.multiplyScalar(dist));
        camera.near = dist / 1000;
        camera.far = dist * 100;
        camera.updateProjectionMatrix();
        camera.lookAt(controls.target);
        controls.update();
        render();
    }

    var VIEWS = {
        iso: new THREE.Vector3(1, 0.8, 1),
        front: new THREE.Vector3(0, 0, 1),
        top: new THREE.Vector3(0, 1, 0.0001),
        right: new THREE.Vector3(1, 0, 0)
    };

    function fitCurrent() {
        var dir = camera.position.clone().sub(controls.target);
        if (dir.lengthSq() === 0) dir.copy(VIEWS.iso);
        setView(dir);
    }

    // ---------- UI overlays ----------
    function makeButton(label, onClick) {
        var b = document.createElement('button');
        b.type = 'button';
        b.textContent = label;
        b.style.cssText = 'padding:3px 9px;margin-right:4px;font-size:12px;line-height:1.4;' +
            'background:#ffffff;color:#1e293b;border:1px solid #94a3b8;border-radius:3px;cursor:pointer;';
        b.addEventListener('click', onClick);
        return b;
    }

    function setActive(btn, active) {
        btn.style.background = active ? '#1e293b' : '#ffffff';
        btn.style.color = active ? '#ffffff' : '#1e293b';
    }

    function buildToolbar() {
        var bar = document.createElement('div');
        bar.style.cssText = 'position:absolute;top:8px;left:8px;z-index:5;display:flex;flex-wrap:wrap;';

        bar.appendChild(makeButton('Fit', fitCurrent));
        bar.appendChild(makeButton('Isometric', function () { setView(VIEWS.iso); }));
        bar.appendChild(makeButton('Front', function () { setView(VIEWS.front); }));
        bar.appendChild(makeButton('Top', function () { setView(VIEWS.top); }));
        bar.appendChild(makeButton('Right', function () { setView(VIEWS.right); }));

        var wireBtn = makeButton('Wireframe', function () {
            wireframeMode = !wireframeMode;
            setActive(wireBtn, wireframeMode);
            parts.forEach(applyPartState);
            render();
        });
        bar.appendChild(wireBtn);

        var tankBtn = makeButton('Transparent tank', function () {
            tankTransparent = !tankTransparent;
            setActive(tankBtn, tankTransparent);
            applyTankTransparency();
            render();
        });
        setActive(tankBtn, true);
        bar.appendChild(tankBtn);

        container.appendChild(bar);
    }

    function buildPartList() {
        var panel = document.createElement('div');
        panel.style.cssText = 'position:absolute;top:8px;right:8px;z-index:5;max-height:80%;overflow:auto;' +
            'background:rgba(255,255,255,0.92);border:1px solid #94a3b8;border-radius:3px;' +
            'padding:6px 10px;font-size:12px;color:#1e293b;min-width:140px;';

        var title = document.createElement('div');
        title.textContent = 'Model';
        title.style.cssText = 'font-weight:600;margin-bottom:4px;';
        panel.appendChild(title);

        parts.forEach(function (p) {
            var row = document.createElement('label');
            row.style.cssText = 'display:flex;align-items:center;gap:6px;margin:2px 0;cursor:pointer;white-space:nowrap;';

            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = true;
            cb.addEventListener('change', function () {
                p.visible = cb.checked;
                applyPartState(p);
                render();
            });

            var text = document.createElement('span');
            text.textContent = p.name;

            row.appendChild(cb);
            row.appendChild(text);
            panel.appendChild(row);
        });

        container.appendChild(panel);
    }

    // ---------- load STEP ----------
    occtimportjs({ locateFile: function (file) { return wasmBase + file; } })
        .then(function (occt) {
            return fetch(stepUrl)
                .then(function (response) {
                    if (!response.ok) throw new Error('STEP download failed (' + response.status + ')');
                    return response.arrayBuffer();
                })
                .then(function (buffer) {
                    var result = occt.ReadStepFile(new Uint8Array(buffer), {
                        linearUnit: 'millimeter',
                        linearDeflectionType: 'bounding_box_ratio',
                        linearDeflection: 0.0003,
                        angularDeflection: 0.1
                    });
                    if (!result.success) throw new Error('STEP parse failed');
                    return result;
                });
        })
        .then(function (result) {
            var names = {};
            collectNames(result.root, names);

            var items = result.meshes.map(function (m, i) {
                var geometry = buildGeometry(m);
                return {
                    name: m.name || names[i] || ('Part ' + (i + 1)),
                    geometry: geometry,
                    radius: geometry.boundingSphere ? geometry.boundingSphere.radius : 0
                };
            });

            // Tank = part named Tank*, else the largest part.
            var tankIndex = -1;
            items.forEach(function (it, i) {
                if (tankIndex < 0 && /^tank/i.test(it.name)) tankIndex = i;
            });
            if (tankIndex < 0) {
                var best = -1;
                items.forEach(function (it, i) {
                    if (it.radius > best) { best = it.radius; tankIndex = i; }
                });
            }

            var root = new THREE.Group();

            items.forEach(function (it, i) {
                var isTank = i === tankIndex;
                var isRotor = /^(shaft|impeller)/i.test(it.name);

                var material = new THREE.MeshLambertMaterial({
                    color: isTank ? 0xaab2b9 : (isRotor ? 0x8f9aa5 : 0xb5bcc3),
                    side: THREE.DoubleSide,
                    polygonOffset: true,
                    polygonOffsetFactor: 1,
                    polygonOffsetUnits: 1
                });

                var edgeMaterial = new THREE.LineBasicMaterial({ color: 0x000000 });

                var mesh = new THREE.Mesh(it.geometry, material);
                var edges = new THREE.LineSegments(new THREE.EdgesGeometry(it.geometry, isTank ? 60 : 25), edgeMaterial);

                root.add(mesh);
                root.add(edges);

                parts.push({
                    name: it.name,
                    mesh: mesh,
                    edges: edges,
                    material: material,
                    edgeMaterial: edgeMaterial,
                    isTank: isTank,
                    visible: true
                });
            });

            applyTankTransparency();

            root.rotation.x = -Math.PI / 2;   // CAD Z-up -> three.js Y-up
            root.updateMatrixWorld(true);

            var box = new THREE.Box3().setFromObject(root);
            var center = box.getCenter(new THREE.Vector3());
            root.position.sub(center);
            scene.add(root);

            var sphere = new THREE.Sphere();
            new THREE.Box3().setFromObject(root).getBoundingSphere(sphere);
            sceneRadius = sphere.radius || 1;

            controls.target.set(0, 0, 0);

            buildToolbar();
            buildPartList();
            setView(VIEWS.iso);
        })
        .catch(function (err) {
            console.error('STEP viewer failed:', err);
            showError('Unable to load 3D model.');
        });

    window.addEventListener('resize', function () {
        var w = container.clientWidth;
        var h = container.clientHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
        render();
    });
})();