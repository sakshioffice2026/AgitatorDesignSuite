// CAD preview viewer.
// Loads the lightweight OBJ preview mesh produced by the FreeCAD job and renders it
// with three.js (WebGL). The full STEP file is NOT parsed in the browser, because
// parsing large STEP files with WebAssembly blocks the page for minutes.
// No animation loop: the scene is redrawn only when the user interacts.
// Expects: <div id="viewer-container" data-preview-url="..." data-step-url="..."></div>
// Requires (loaded before this file): three r128 and OrbitControls r128.
(function () {
    'use strict';

    var container = document.getElementById('viewer-container');
    if (!container) return;

    var previewUrl = container.getAttribute('data-preview-url');

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
    function showMessage(message, isError) {
        container.innerHTML = '<p class="' + (isError ? 'text-danger' : 'text-muted') +
            ' p-3 mb-0">' + message + '</p>';
    }

    // Parses the OBJ written by the FreeCAD job:
    //   "o <name>", global "v x y z" lines, and "f a b c" triangles (1-based global indices).
    function parseObj(text) {
        var verts = [];
        var objects = [];
        var current = null;

        var lines = text.split('\n');
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            var c = line.charCodeAt(0);

            if (c === 118 && line.charCodeAt(1) === 32) {               // "v "
                var v = line.split(' ');
                verts.push(parseFloat(v[1]), parseFloat(v[2]), parseFloat(v[3]));
            } else if (c === 102 && line.charCodeAt(1) === 32) {        // "f "
                if (!current) { current = { name: 'Part ' + (objects.length + 1), tris: [] }; objects.push(current); }
                var f = line.trim().split(/\s+/);
                var a = parseInt(f[1], 10) - 1;
                var b = parseInt(f[2], 10) - 1;
                var d = parseInt(f[3], 10) - 1;
                current.tris.push(a, b, d);
            } else if (c === 111 && line.charCodeAt(1) === 32) {        // "o "
                current = { name: line.substring(2).trim() || ('Part ' + (objects.length + 1)), tris: [] };
                objects.push(current);
            }
        }

        return { verts: verts, objects: objects };
    }

    function buildGeometry(verts, tris) {
        var positions = new Float32Array(tris.length * 3);
        for (var i = 0; i < tris.length; i++) {
            var vi = tris[i] * 3;
            positions[i * 3] = verts[vi];
            positions[i * 3 + 1] = verts[vi + 1];
            positions[i * 3 + 2] = verts[vi + 2];
        }
        var geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        geometry.computeVertexNormals();
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

    // ---------- load OBJ preview ----------
    if (!previewUrl) {
        showMessage('No lightweight preview is available for this model. Use "Download STEP file" below to open it in a CAD viewer.', false);
        return;
    }

    fetch(previewUrl, { credentials: 'same-origin' })
        .then(function (response) {
            if (!response.ok) throw new Error('Preview download failed (' + response.status + ')');
            return response.text();
        })
        .then(function (text) {
            var parsed = parseObj(text);
            var items = [];

            parsed.objects.forEach(function (o) {
                if (o.tris.length === 0) return;
                var geometry = buildGeometry(parsed.verts, o.tris);
                items.push({
                    name: o.name,
                    geometry: geometry,
                    radius: geometry.boundingSphere ? geometry.boundingSphere.radius : 0
                });
            });

            if (items.length === 0) throw new Error('Preview contains no geometry');

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
            console.error('CAD preview failed:', err);
            showMessage('Unable to load the 3D preview. Use "Download STEP file" below to open the model in a CAD viewer.', true);
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