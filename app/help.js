// Ayudas en lenguaje simple para AI Launcher.
// `simple: true` = aparece en el "modo simple" (lo que conviene conocer primero).
window.HELP = (() => {

  // ------------------------------------------------------------------ glosario
  const glossary = {
    "Modelo": "El “cerebro” ya entrenado que usa el programa. Un modelo de texto (Ollama) responde preguntas; un modelo de imagen (checkpoint) dibuja.",
    "Checkpoint": "Archivo grande (2–7 GB) con un modelo de imagen completo. Define el estilo general: realista, anime, pixel art, etc.",
    "LoRA": "Archivo chico (10–200 MB) que se suma a un checkpoint para enseñarle algo puntual: un personaje, un estilo, un objeto. Kohya_ss sirve para crear LoRAs.",
    "VRAM": "Memoria de la placa de video. En las Mac con chip Apple es la misma que la RAM: todo lo que abras compite por ella.",
    "RAM": "Memoria de trabajo de la computadora. Si se llena, todo se pone muy lento.",
    "Contexto": "Cuánto texto “recuerda” un modelo de chat a la vez (la conversación + documentos). Más contexto = más memoria usada.",
    "Cuantización": "Versión “comprimida” de un modelo (Q4, Q8…). Ocupa menos memoria a cambio de un poco de calidad. Q4_K_M es un buen equilibrio.",
    "Parámetros": "Tamaño del modelo (7B = 7 mil millones). Más grande suele ser más inteligente pero más lento y pesado.",
    "Puerto": "Número que identifica a un programa dentro de tu compu (ej.: 7860). Dos programas no pueden usar el mismo puerto.",
    "API": "Forma en que otros programas pueden usar éste automáticamente, sin la interfaz.",
    "Token": "Pedacito de texto (aprox. ¾ de una palabra). La velocidad de un modelo se mide en tokens por segundo.",
    "SD 1.5 / SDXL / Flux": "Generaciones de modelos de imagen. SD 1.5 es liviano (512px); SDXL da mejor calidad (1024px) y usa más memoria; Flux es más nuevo y muy pesado.",
  };

  // ------------------------------------------------------------------ guías por sección
  const sections = {
    monitor: `<b>Qué es esto:</b> lo que está corriendo ahora y cuánta memoria usa. Si la compu anda lenta, mirá acá qué ocupa más y detené lo que no estés usando.
      <br><b>Detener</b> le pide al programa que se cierre bien. <b>Forzar</b> lo corta de golpe (usalo sólo si “Detener” no funcionó).`,
    ollama: `<b>Ollama</b> corre modelos de <b>chat/texto</b> en tu compu, sin internet y sin mandar tus datos a nadie (como un ChatGPT local).
      <br><b>Cómo se usa:</b> 1) tocá <b>Iniciar</b>, 2) descargá un modelo (abajo), 3) tocá <b>Chat en terminal</b> o usalo desde otra app compatible con Ollama.
      <br><b>Las opciones</b> son para casos especiales: si no sabés qué tocar, dejalas como están.`,
    a1111: `<b>Stable Diffusion WebUI (AUTOMATIC1111)</b> genera <b>imágenes a partir de texto</b> (o modifica imágenes). Se usa desde el navegador.
      <br><b>Cómo se usa:</b> 1) tocá <b>Iniciar</b> (la primera vez tarda unos minutos), 2) <b>Abrir en el navegador</b>, 3) escribí qué querés ver en “prompt” y tocá <i>Generate</i>.`,
    forge: `<b>Forge</b> es una versión de Stable Diffusion WebUI optimizada para usar menos memoria y ser más rápida. Se usa igual que AUTOMATIC1111.`,
    comfyui: `<b>ComfyUI</b> genera imágenes armando un “diagrama” de bloques conectados. Es más flexible (y más complejo) que A1111. Ideal para flujos avanzados o modelos nuevos como Flux.`,
    kohya: `<b>Kohya_ss</b> sirve para <b>entrenar</b> tus propios LoRAs: le das 15–50 imágenes de algo (un personaje, un estilo) y aprende a dibujarlo.
      <br>Entrenar usa <b>mucha memoria durante bastante tiempo</b> (de minutos a horas). Mientras entrena, conviene no tener Stable Diffusion ni modelos grandes de Ollama abiertos.`,
  };

  // ------------------------------------------------------------------ variables de Ollama
  const ollama = {
    OLLAMA_HOST: { simple: true, text: "Dirección donde escucha Ollama. Por defecto sólo tu compu puede usarlo. Poné <code>0.0.0.0:11434</code> para usarlo desde otros dispositivos de tu red (celular, otra compu).", tip: "Si no lo vas a usar desde otro dispositivo, dejalo vacío." },
    OLLAMA_KEEP_ALIVE: { simple: true, text: "Cuánto tiempo queda un modelo cargado en memoria después de usarlo. Mientras está cargado responde al instante, pero ocupa memoria.", tip: "<code>5m</code> = 5 minutos (normal). <code>0</code> = liberar enseguida (ahorra memoria). <code>-1</code> = nunca liberar." },
    OLLAMA_CONTEXT_LENGTH: { simple: true, text: "Cuánto texto puede “recordar” el modelo en una conversación. Más alto permite charlas o documentos más largos, pero usa más memoria.", tip: "4096 alcanza para charlas normales. 16384 o más para leer documentos largos." },
    OLLAMA_MODELS: { simple: true, text: "Carpeta donde se guardan los modelos descargados. Cambiala si querés guardarlos en un disco externo con más espacio.", tip: "Los modelos ocupan varios GB cada uno." },
    OLLAMA_MAX_LOADED_MODELS: { simple: false, text: "Cuántos modelos distintos pueden estar cargados a la vez. Cada uno ocupa su propia memoria.", tip: "Con 16 GB o menos, 1 es lo más seguro." },
    OLLAMA_NUM_PARALLEL: { simple: false, text: "Cuántas preguntas atiende a la vez el mismo modelo. Sólo sirve si varias apps o personas lo usan al mismo tiempo; cada una suma memoria.", tip: "Para uso personal, 1." },
    OLLAMA_MAX_QUEUE: { simple: false, text: "Cuántos pedidos pueden esperar en fila antes de que Ollama empiece a rechazarlos." },
    OLLAMA_FLASH_ATTENTION: { simple: true, text: "Optimización que reduce la memoria usada y suele acelerar las respuestas, sobre todo con contextos largos.", tip: "Recomendado activarlo. Si notás errores raros, desactivalo." },
    OLLAMA_KV_CACHE_TYPE: { simple: false, text: "Comprime la “memoria de la conversación”. <code>q8_0</code> usa la mitad de memoria casi sin perder calidad; <code>q4_0</code> aún menos pero se nota más.", tip: "Necesita Flash Attention activado. Útil si te falta memoria con contextos largos." },
    OLLAMA_ORIGINS: { simple: false, text: "Qué páginas web pueden usar Ollama desde el navegador. Necesario para algunas extensiones o apps web.", tip: "Ej.: <code>*</code> permite todas (menos seguro)." },
    OLLAMA_DEBUG: { simple: false, text: "Muestra información técnica detallada en los logs. Útil para diagnosticar problemas." },
    OLLAMA_NOPRUNE: { simple: false, text: "Evita que Ollama borre al arrancar archivos de modelos que quedaron incompletos o sin uso." },
    OLLAMA_SCHED_SPREAD: { simple: false, text: "Reparte cada modelo entre todas las placas de video (sólo si tenés más de una)." },
    OLLAMA_GPU_OVERHEAD: { simple: false, text: "Memoria de video que Ollama deja libre a propósito (en bytes), para que otros programas no se queden sin." },
    OLLAMA_LOAD_TIMEOUT: { simple: false, text: "Cuánto espera Ollama a que un modelo termine de cargar antes de darse por vencido. Subilo si tu disco es lento o el modelo es enorme." },
    OLLAMA_NO_CLOUD: { simple: false, text: "Desactiva las funciones en la nube de Ollama (modelos remotos y búsqueda web). Todo queda 100% local." },
    OLLAMA_LLM_LIBRARY: { simple: false, text: "Fuerza un motor de cálculo específico en lugar de que Ollama elija solo. Sólo para diagnóstico." },
    OLLAMA_IGPU_ENABLE: { simple: false, text: "Permite usar la placa de video integrada (de PCs con Windows/Linux)." },
    OLLAMA_MAX_TRANSFER_STREAMS: { simple: false, text: "Cuántas partes de un modelo se descargan en paralelo. Más puede descargar más rápido si tu internet es bueno." },
    LLAMA_ARG_FIT: { simple: false, text: "Ajusta automáticamente cuánta memoria usa cada modelo para que entre en tu equipo." },
    LLAMA_ARG_FIT_TARGET: { simple: false, text: "Cuánta memoria de video (en MiB) dejar libre al hacer ese ajuste automático." },
  };

  // ------------------------------------------------------------------ opciones de imagen
  // Las claves son las banderas tal cual; `only` limita a ciertos programas.
  const sd = {
    // --- red y acceso
    "--api": { simple: true, text: "Permite que otros programas (y este launcher) controlen la WebUI: ver qué modelo está cargado, liberarlo de memoria, generar imágenes automáticamente.", tip: "Recomendado activarlo." },
    "--listen": { simple: true, text: "Permite abrir la interfaz desde otros dispositivos de tu red (celular, tablet, otra compu) usando la IP de esta compu.", tip: "Dejalo apagado si sólo la usás en esta compu." },
    "--port": { simple: true, text: "Número de puerto donde se abre la interfaz (la dirección queda <code>http://127.0.0.1:PUERTO</code>). Cambialo si querés tener dos programas abiertos a la vez.", tip: "Vacío = el de siempre (7860 en A1111/Forge, 8188 en ComfyUI)." },
    "--server_port": { simple: true, text: "Número de puerto donde se abre la interfaz de Kohya. Cambialo si ya tenés Stable Diffusion abierto en el 7860.", tip: "Vacío = 7860 o el siguiente libre." },
    "--autolaunch": { simple: true, text: "Abre la interfaz en el navegador automáticamente cuando termina de arrancar." },
    "--auto-launch": { simple: true, text: "Abre ComfyUI en el navegador automáticamente al arrancar." },
    "--inbrowser": { simple: true, text: "Abre Kohya en el navegador automáticamente al arrancar." },
    "--share": { simple: false, text: "Crea un enlace público temporal en internet (gradio.live) para usar la interfaz desde cualquier lugar.", tip: "⚠️ Cualquiera con el enlace puede usar tu compu para generar. Usalo con cuidado." },
    "--gradio-auth": { simple: false, text: "Pide usuario y contraseña para entrar a la interfaz. Formato: <code>usuario:contraseña</code>.", tip: "Recomendado si activás --listen o --share." },
    "--username": { simple: false, text: "Usuario para entrar a Kohya (junto con --password). Útil si lo abrís en red." },
    "--password": { simple: false, text: "Contraseña para entrar a Kohya." },
    "--headless": { simple: false, text: "Para usar Kohya en un servidor sin pantalla: oculta botones que abren ventanas para elegir archivos." },
    "--theme": { simple: true, text: "Colores de la interfaz: claro (<code>light</code>) u oscuro (<code>dark</code>)." },
    "--language": { simple: false, text: "Idioma de la interfaz de Kohya (si hay traducción disponible)." },
    "--enable-insecure-extension-access": { simple: false, text: "Permite instalar extensiones desde la interfaz aunque esté abierta en red.", tip: "⚠️ Riesgoso si otras personas tienen acceso." },
    "--nowebui": { simple: false, text: "Arranca sólo la API, sin la interfaz web. Para usarla desde otros programas." },
    "--cors-allow-origins": { simple: false, text: "Qué páginas web pueden usar la API desde el navegador." },

    // --- memoria y rendimiento
    "--medvram": { simple: true, text: "Ahorra memoria cargando partes del modelo sólo cuando se necesitan. Genera un poco más lento.", tip: "Probalo si aparece “out of memory” con modelos SD 1.5." },
    "--medvram-sdxl": { simple: true, text: "Lo mismo que --medvram pero sólo se activa con modelos SDXL (los grandes, de 1024px).", tip: "Recomendado en compus con 16 GB o menos si usás SDXL." },
    "--lowvram": { simple: true, text: "Ahorro de memoria extremo. Mucho más lento.", tip: "Sólo si --medvram no alcanza." },
    "--lowram": { simple: false, text: "Carga los modelos directo a la memoria de video en vez de pasar por la RAM. Útil en PCs con poca RAM y buena placa de video." },
    "--no-half": { simple: true, text: "Calcula con más precisión (32 bits). Evita imágenes negras o errores en algunos equipos, pero usa el doble de memoria y es más lento.", tip: "En Mac con chip Apple suele hacer falta para modelos SD 1.5." },
    "--no-half-vae": { simple: true, text: "Lo mismo que --no-half pero sólo para la parte que convierte el resultado en imagen (VAE). Arregla imágenes negras gastando poca memoria extra.", tip: "Ya viene activado en Mac." },
    "--upcast-sampling": { simple: true, text: "Alternativa más liviana a --no-half: evita errores de precisión usando menos memoria.", tip: "Ya viene activado en Mac." },
    "--precision": { simple: false, text: "Precisión numérica de los cálculos. <code>autocast</code> elige sola; <code>full</code> es más exacta pero más lenta." },
    "--opt-sub-quad-attention": { simple: true, text: "Optimización que reduce mucho la memoria al generar imágenes grandes.", tip: "Buena opción en Mac y en placas AMD." },
    "--opt-split-attention": { simple: false, text: "Optimización de memoria clásica. Normalmente se elige sola." },
    "--opt-split-attention-v1": { simple: false, text: "Versión vieja de la optimización anterior: usa menos memoria pero es más lenta." },
    "--opt-sdp-attention": { simple: false, text: "Optimización moderna de PyTorch, rápida en placas NVIDIA recientes." },
    "--opt-sdp-no-mem-attention": { simple: false, text: "Como la anterior pero con resultados idénticos en cada generación (más reproducible)." },
    "--xformers": { simple: false, text: "Optimización para placas NVIDIA que acelera y ahorra memoria.", tip: "Sólo NVIDIA. En Mac no tiene efecto." },
    "--disable-nan-check": { simple: false, text: "Desactiva el control que detecta imágenes negras/rotas. Sólo para diagnóstico." },
    "--use-cpu": { simple: false, text: "Usa el procesador (no la placa de video) para ciertas partes. Mucho más lento; sólo para equipos sin placa compatible." },
    "--skip-load-model-at-start": { simple: true, text: "No carga ningún modelo al arrancar: la interfaz abre más rápido y no ocupa memoria hasta que elijas uno." },
    "--ckpt": { simple: true, text: "Qué modelo (checkpoint) cargar al arrancar. Define el estilo de las imágenes.", tip: "Si lo dejás vacío, usa el último que elegiste en la interfaz." },
    "--cpu": { simple: false, text: "Hace todo con el procesador. Muy lento; sólo si no tenés placa de video compatible." },
    "--highvram": { simple: false, text: "Mantiene todos los modelos en la memoria de video para ir más rápido. Sólo con placas de mucha memoria." },
    "--normalvram": { simple: false, text: "Fuerza el uso normal de memoria si ComfyUI eligió el modo bajo por error." },
    "--force-fp16": { simple: false, text: "Fuerza cálculos de 16 bits: ahorra memoria pero puede dar errores en algunos equipos." },
    "--preview-method": { simple: true, text: "Cómo mostrar la imagen mientras se va generando. <code>auto</code> es lo más cómodo; <code>none</code> ahorra un poco de tiempo." },

    // --- rutas
    "--data-dir": { simple: false, text: "Carpeta base donde se guardan modelos, configuración y resultados." },
    "--models-dir": { simple: false, text: "Carpeta donde están los modelos. Útil para compartir modelos entre varias instalaciones o guardarlos en un disco externo." },
    "--ckpt-dir": { simple: false, text: "Carpeta extra donde buscar checkpoints (modelos de imagen)." },
    "--lora-dir": { simple: false, text: "Carpeta extra donde buscar LoRAs." },
    "--vae-dir": { simple: false, text: "Carpeta donde buscar archivos VAE (afinan los colores y detalles finales)." },
    "--embeddings-dir": { simple: false, text: "Carpeta de embeddings (palabras clave entrenadas, por ejemplo para mejorar manos o evitar defectos)." },
    "--output-directory": { simple: false, text: "Carpeta donde ComfyUI guarda las imágenes generadas." },
    "--config": { simple: false, text: "Archivo de configuración a usar." },

    // --- arranque
    "--skip-torch-cuda-test": { simple: false, text: "Salta la comprobación de placa NVIDIA. Necesario en Mac (ya viene activado)." },
    "--skip-install": { simple: false, text: "No revisa ni instala dependencias al arrancar: arranca más rápido, pero no se actualiza nada." },
    "--skip-prepare-environment": { simple: false, text: "Salta toda la preparación inicial. Arranca más rápido si ya está todo instalado." },
    "--skip-version-check": { simple: false, text: "No comprueba las versiones de los componentes al arrancar." },
    "--update-check": { simple: false, text: "Avisa al arrancar si hay una versión nueva." },
    "--update-all-extensions": { simple: false, text: "Actualiza todas las extensiones al arrancar." },
    "--reinstall-torch": { simple: false, text: "Reinstala PyTorch (el motor de cálculo). Usalo si algo se rompió después de una actualización." },
    "--disable-all-extensions": { simple: false, text: "Arranca sin ninguna extensión. Útil para saber si una extensión está causando un problema." },
    "--disable-extra-extensions": { simple: false, text: "Arranca sólo con las extensiones que vienen incluidas." },
    "--loglevel": { simple: false, text: "Cuánto detalle mostrar en los logs (DEBUG = todo, ERROR = sólo errores)." },
    "--debug": { simple: false, text: "Muestra información técnica detallada en los logs." },
    "--noverify": { simple: false, text: "Kohya no verifica las dependencias al arrancar (más rápido)." },
    "--do_not_share": { simple: false, text: "Kohya: oculta la opción de compartir públicamente." },
    "--requirements": { simple: false, text: "Kohya: archivo de dependencias a usar (lo elige solo según tu sistema)." },
    "--use-ipex": { simple: false, text: "Usa placas de video Intel Arc." },
    "--use-rocm": { simple: false, text: "Usa placas de video AMD (Linux)." },
  };

  // ------------------------------------------------------------------ para qué sirve cada app
  const purpose = {
    ollama: "Chat con inteligencia artificial en tu compu, como ChatGPT pero sin internet y privado.",
    a1111: "Crea imágenes a partir de una descripción en texto. Se usa desde el navegador.",
    forge: "Crea imágenes a partir de texto, como A1111 pero más rápido y con menos memoria.",
    comfyui: "Crea imágenes armando flujos con bloques. Más flexible, para usuarios avanzados.",
    kohya: "Entrena tus propios estilos o personajes (LoRA) para después usarlos al crear imágenes.",
  };

  // ------------------------------------------------------------------ modelos de Ollama
  const families = [
    [/coder|code|starcoder|codestral|devstral/i, "💻 Especializado en <b>programación</b>: escribe, explica y corrige código."],
    [/embed|nomic|bge|mxbai|minilm/i, "🔎 Modelo de <b>embeddings</b>: no sirve para chatear; convierte textos en números para buscar documentos (lo usan apps de “chat con tus PDFs”)."],
    [/llava|vision|-vl\b|moondream|bakllava|minicpm-v/i, "👁️ Puede <b>ver imágenes</b>: describirlas, leer texto en fotos, responder preguntas sobre ellas."],
    [/deepseek-r1|qwq|reason|thinking|magistral/i, "🧠 Modelo de <b>razonamiento</b>: “piensa” antes de responder. Mejor para matemática y lógica, pero más lento."],
    [/math/i, "➗ Especializado en <b>matemática</b>."],
    [/qwen/i, "💬 Modelo de chat general de Alibaba (Qwen). Muy bueno en español, programación y seguir instrucciones."],
    [/llama/i, "💬 Modelo de chat general de Meta (Llama). Bueno para conversación y tareas variadas."],
    [/gemma/i, "💬 Modelo de chat general de Google (Gemma). Equilibrado y eficiente."],
    [/mistral|mixtral|ministral/i, "💬 Modelo de chat general de Mistral. Rápido y bueno en idiomas europeos."],
    [/phi/i, "💬 Modelo chico de Microsoft (Phi). Rápido, ideal para compus con poca memoria."],
    [/deepseek/i, "💬 Modelo de DeepSeek, fuerte en razonamiento y programación."],
    [/gpt-oss/i, "💬 Modelo abierto de OpenAI, bueno en razonamiento y uso de herramientas."],
  ];
  const caps = {
    vision: "👁️ entiende imágenes", tools: "🛠️ puede usar herramientas (lo usan agentes y automatizaciones)",
    thinking: "🧠 razona paso a paso", embedding: "🔎 embeddings", completion: null, insert: null,
  };
  function sizeAdvice(paramStr, bytes, totalMem) {
    const b = parseFloat(String(paramStr || "").replace(/[^\d.]/g, "")) || 0;
    const sizeTxt = b ? (b < 4 ? "Chico: rápido, respuestas más simples." : b < 15 ? "Mediano: buen equilibrio entre calidad y velocidad." : b < 40 ? "Grande: respuestas de mejor calidad, más lento." : "Muy grande: necesita mucha memoria.") : "";
    let fit = "";
    if (bytes && totalMem) {
      const ratio = bytes / totalMem;
      fit = ratio > 0.7 ? " ⚠️ Ocupa casi toda tu memoria: puede andar muy lento." : ratio > 0.45 ? " Ocupa bastante de tu memoria: cerrá otras cosas pesadas al usarlo." : " Entra cómodo en tu memoria.";
    }
    return sizeTxt + fit;
  }
  function ollamaModel(m, totalMem) {
    const name = m.name || "";
    const info = m.info || {};
    const parts = [];
    const fam = families.find(([re]) => re.test(name) || re.test(info.family || ""));
    parts.push(fam ? fam[1] : "💬 Modelo de chat general.");
    const cs = (info.capabilities || []).map(c => caps[c]).filter(Boolean);
    if (cs.length) parts.push("Capacidades: " + cs.join(" · ") + ".");
    const adv = sizeAdvice(info.parameter_size || m.details?.parameter_size, m.size, totalMem);
    if (adv) parts.push(adv);
    if (info.context_length) parts.push("Contexto máximo: " + Number(info.context_length).toLocaleString("es") + " tokens.");
    return parts.join(" ");
  }

  // ------------------------------------------------------------------ checkpoints de imagen
  function checkpoint(name, size) {
    const n = name.toLowerCase();
    const out = [];
    if (/flux/.test(n)) out.push("Modelo <b>Flux</b>: muy buena calidad y entiende bien textos largos; necesita mucha memoria.");
    else if (/sdxl|xl|pony|illustrious|noob/.test(n) || size > 5e9) out.push("Modelo <b>SDXL</b>: imágenes de 1024px con buena calidad. Usá tamaños como 1024×1024.");
    else if (/sd3/.test(n)) out.push("Modelo <b>SD3</b>.");
    else out.push("Probablemente <b>SD 1.5</b>: liviano, imágenes de 512px. Usá tamaños como 512×512 o 512×768.");
    if (/pixel/.test(n)) out.push("Estilo <b>pixel art</b> (ideal para sprites de juegos).");
    if (/anime|anything|counterfeit|pony|illustrious|noob/.test(n)) out.push("Estilo <b>anime/ilustración</b>.");
    if (/real|photo|juggernaut|epic/.test(n)) out.push("Estilo <b>fotorrealista</b>.");
    if (/inpaint/.test(n)) out.push("Versión especial para <b>inpainting</b> (corregir partes de una imagen).");
    if (/sprite|shaper/.test(n)) out.push("Pensado para <b>sprites</b> y personajes de juegos.");
    return out.join(" ");
  }

  // modelos sugeridos para descargar en Ollama, según la memoria
  function suggestions(totalMem) {
    const gb = totalMem / 2 ** 30;
    const list = [
      ["gemma3:1b", "chico y muy rápido, para probar", 1],
      ["llama3.2", "chat general liviano (3B)", 4],
      ["qwen3:4b", "chat en español, buen equilibrio", 6],
      ["qwen2.5-coder:7b", "ayuda para programar", 8],
      ["gemma3:12b", "chat general de mejor calidad, entiende imágenes", 14],
      ["qwen3:14b", "chat avanzado y razonamiento", 16],
      ["qwen3:32b", "muy buena calidad (lento)", 32],
    ];
    return list.filter(x => x[2] <= gb).slice(-5);
  }

  return { glossary, sections, ollama, sd, purpose, ollamaModel, checkpoint, suggestions };
})();
