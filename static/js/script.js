// ============================================================
// IA DOCUMENTOS AGRÍCOLAS
// SCRIPT PRINCIPAL
// ============================================================


// ============================================================
// ELEMENTOS DA PÁGINA
// ============================================================

const arquivoInput =
    document.getElementById("arquivo");

const dropzone =
    document.getElementById("dropzone");

const btnProcessar =
    document.getElementById("btnProcessar");

const arquivoSelecionado =
    document.getElementById("arquivoSelecionado");

const loading =
    document.getElementById("loading");

const resultado =
    document.getElementById("resultado");


// ============================================================
// ESTADO
// ============================================================

let arquivoAtual = null;

let documentoAtual = null;

let documentoIdAtual = null;

let totalCorrecoes = 0;


// ============================================================
// SELEÇÃO DO ARQUIVO
// ============================================================

dropzone.addEventListener(
    "click",
    () => {

        arquivoInput.click();

    }
);


// ============================================================
// INPUT DE ARQUIVO
// ============================================================

arquivoInput.addEventListener(
    "change",
    () => {

        if (
            arquivoInput.files.length > 0
        ) {

            arquivoAtual =
                arquivoInput.files[0];

            mostrarArquivo();

        }

    }
);


// ============================================================
// DRAG
// ============================================================

dropzone.addEventListener(
    "dragover",
    event => {

        event.preventDefault();

        dropzone.classList.add(
            "dragging"
        );

    }
);


// ============================================================
// DRAG LEAVE
// ============================================================

dropzone.addEventListener(
    "dragleave",
    () => {

        dropzone.classList.remove(
            "dragging"
        );

    }
);


// ============================================================
// DROP
// ============================================================

dropzone.addEventListener(
    "drop",
    event => {

        event.preventDefault();

        dropzone.classList.remove(
            "dragging"
        );


        if (
            event.dataTransfer.files.length > 0
        ) {

            arquivoAtual =
                event.dataTransfer.files[0];

            mostrarArquivo();

        }

    }
);


// ============================================================
// MOSTRAR ARQUIVO
// ============================================================

function mostrarArquivo() {

    if (!arquivoAtual) {

        arquivoSelecionado.innerHTML =
            "";

        return;

    }


    arquivoSelecionado.innerHTML = `

        <strong>
            Arquivo selecionado:
        </strong>

        ${escapeHtml(
            arquivoAtual.name
        )}

    `;

}


// ============================================================
// BOTÃO PROCESSAR
// ============================================================

btnProcessar.addEventListener(
    "click",
    processar
);


// ============================================================
// PROCESSAR DOCUMENTO
// ============================================================

async function processar() {

    if (!arquivoAtual) {

        alert(
            "Selecione um documento primeiro."
        );

        return;

    }


    resultado.classList.add(
        "hidden"
    );

    loading.classList.remove(
        "hidden"
    );

    btnProcessar.disabled = true;


    const formData =
        new FormData();


    formData.append(
        "arquivo",
        arquivoAtual
    );


    try {

        const resposta =
            await fetch(
                "/api/processar",
                {
                    method: "POST",
                    body: formData
                }
            );


        const dados =
            await resposta.json();


        if (
            !resposta.ok ||
            !dados.sucesso
        ) {

            throw new Error(

                dados.erro ||
                "Erro ao processar documento."

            );

        }


        documentoAtual =
            dados.resultado;


        documentoIdAtual =
            dados.documento_id;


        totalCorrecoes = 0;


        renderizarResultado(
            dados
        );


        carregarEstatisticas();


    } catch (erro) {

        console.error(
            erro
        );


        alert(
            "Erro: " +
            erro.message
        );


    } finally {

        loading.classList.add(
            "hidden"
        );

        btnProcessar.disabled = false;

    }

}


// ============================================================
// RENDERIZAR RESULTADO
// ============================================================

function renderizarResultado(
    dados
) {

    const documento =
        dados.resultado || {};


    documentoAtual =
        documento;


    resultado.classList.remove(
        "hidden"
    );


    document.getElementById(
        "nomeDocumento"
    ).textContent =
        dados.arquivo || "Documento";


    const btnDownload =
        document.getElementById(
            "btnDownload"
        );


    if (
        btnDownload &&
        dados.download
    ) {

        btnDownload.href =
            dados.download;

    }


    const blocos =
        documento.blocos || [];


    let totalTalhoes = 0;


    blocos.forEach(
        bloco => {

            totalTalhoes += (
                bloco.talhoes || []
            ).length;

        }
    );


    document.getElementById(
        "totalBlocos"
    ).textContent =
        blocos.length;


    document.getElementById(
        "totalTalhoes"
    ).textContent =
        totalTalhoes;


    document.getElementById(
        "totalCorrecoes"
    ).textContent =
        totalCorrecoes;


    renderizarAlertas(
        documento.avisos || []
    );


    renderizarMetadata(
        documento.metadata || {}
    );


    renderizarBlocos(
        blocos
    );


    resultado.scrollIntoView({
        behavior: "smooth"
    });

}


// ============================================================
// ALERTAS
// ============================================================

function renderizarAlertas(
    avisos
) {

    const container =
        document.getElementById(
            "alertas"
        );


    if (!container) {

        return;

    }


    if (
        !avisos ||
        avisos.length === 0
    ) {

        container.innerHTML = `

            <div class="alerta ok">

                ✓ Nenhuma inconsistência
                básica encontrada.

            </div>

        `;

        return;

    }


    container.innerHTML =
        avisos.map(
            aviso => `

                <div class="alerta">

                    ⚠
                    ${escapeHtml(
                        String(aviso)
                    )}

                </div>

            `
        ).join("");

}


// ============================================================
// METADADOS
// SOMENTE OS CAMPOS IMPORTANTES
// ============================================================

function renderizarMetadata(
    metadata
) {

    const container =
        document.getElementById(
            "metadataGrid"
        );


    if (!container) {

        return;

    }


    const bloco =
        metadata.bloco || "";


    const propriedade =
        metadata.propriedade || "";


    container.innerHTML = `

        ${criarCampoMetadata(
            "Bloco",
            bloco
        )}

        ${criarCampoMetadata(
            "Propriedade",
            propriedade
        )}

    `;

}


// ============================================================
// CAMPO DE METADATA
// ============================================================

function criarCampoMetadata(
    nome,
    valor
) {

    const vazio =
        !valor ||
        String(valor).trim() === "";


    return `

        <div class="meta-item">

            <label>
                ${nome}
            </label>

            <strong
                ${vazio
                    ? 'class="campo-vazio"'
                    : ''
                }
            >

                ${
                    vazio
                    ? "—"
                    : escapeHtml(
                        String(valor)
                    )
                }

            </strong>

        </div>

    `;

}


// ============================================================
// RENDERIZAR BLOCOS
// ============================================================

function renderizarBlocos(
    blocos
) {

    const container =
        document.getElementById(
            "tabelasBlocos"
        );


    if (!container) {

        return;

    }


    if (
        !blocos ||
        blocos.length === 0
    ) {

        container.innerHTML = `

            <div class="alerta">

                Nenhuma tabela foi
                identificada.

            </div>

        `;

        return;

    }


    container.innerHTML =
        blocos.map(
            (bloco, indiceBloco) => {

                return renderizarBloco(
                    bloco,
                    indiceBloco
                );

            }
        ).join("");

}


// ============================================================
// RENDERIZAR UM BLOCO
// ============================================================

function renderizarBloco(
    bloco,
    indiceBloco
) {

    const talhoes =
        bloco.talhoes || [];


    const codigo =
        bloco.bloco ||
        bloco.codigo ||
        "";


    return `

        <div
            class="tabela-bloco"
            data-bloco-index="${indiceBloco}"
        >

            <div class="tabela-titulo">

                Bloco:
                ${
                    codigo
                    ? escapeHtml(
                        String(codigo)
                    )
                    : "Não identificado"
                }

            </div>


            <table>

                <thead>

                    <tr>

                        <th>
                            Talhão
                        </th>

                        <th>
                            Variedade
                        </th>

                        <th>
                            Área
                        </th>

                        <th>
                            Plantio
                        </th>

                    </tr>

                </thead>


                <tbody>

                    ${
                        talhoes.map(
                            (
                                talhao,
                                indiceTalhao
                            ) => {

                                return renderizarTalhao(
                                    talhao,
                                    indiceBloco,
                                    indiceTalhao
                                );

                            }
                        ).join("")
                    }

                </tbody>

            </table>


            <div
                class="acoes-bloco"
            >

                <button
                    type="button"
                    class="btn-confirmar-bloco"
                    onclick="confirmarBloco(
                        ${indiceBloco}
                    )"
                >

                    ✓ Confirmar bloco

                </button>

            </div>

        </div>

    `;

}


// ============================================================
// RENDERIZAR TALHÃO
// ============================================================

function renderizarTalhao(
    talhao,
    indiceBloco,
    indiceTalhao
) {

    const numero =
        talhao.talhao || "";


    const variedade =
        talhao.variedade || "";


    const area =
        talhao.area || "";


    const plantio =
        talhao.plantio || "";


    return `

        <tr
            data-bloco="${indiceBloco}"
            data-talhao="${indiceTalhao}"
        >

            <td>

                <input
                    type="text"
                    class="campo-tabela"
                    value="${escapeAttribute(
                        numero
                    )}"
                    data-campo="talhao"
                    onchange="campoAlterado(
                        ${indiceBloco},
                        ${indiceTalhao},
                        'talhao',
                        this
                    )"
                >

            </td>


            <td>

                <input
                    type="text"
                    class="campo-tabela"
                    value="${escapeAttribute(
                        variedade
                    )}"
                    data-campo="variedade"
                    onchange="campoAlterado(
                        ${indiceBloco},
                        ${indiceTalhao},
                        'variedade',
                        this
                    )"
                >

            </td>


            <td>

                <input
                    type="text"
                    class="campo-tabela"
                    value="${escapeAttribute(
                        area
                    )}"
                    data-campo="area"
                    onchange="campoAlterado(
                        ${indiceBloco},
                        ${indiceTalhao},
                        'area',
                        this
                    )"
                >

            </td>


            <td>

                <input
                    type="text"
                    class="campo-tabela"
                    value="${escapeAttribute(
                        plantio
                    )}"
                    data-campo="plantio"
                    onchange="campoAlterado(
                        ${indiceBloco},
                        ${indiceTalhao},
                        'plantio',
                        this
                    )"
                >

            </td>

        </tr>

    `;

}


// ============================================================
// CAMPO ALTERADO
// ============================================================

async function campoAlterado(
    indiceBloco,
    indiceTalhao,
    campo,
    elemento
) {

    if (
        !documentoAtual ||
        !documentoAtual.blocos
    ) {

        return;

    }


    const bloco =
        documentoAtual.blocos[
            indiceBloco
        ];


    if (!bloco) {

        return;

    }


    const talhao =
        (bloco.talhoes || [])[
            indiceTalhao
        ];


    if (!talhao) {

        return;

    }


    const valorAnterior =
        talhao[campo] || "";


    const valorNovo =
        elemento.value.trim();


    if (
        valorAnterior ===
        valorNovo
    ) {

        return;

    }


    // Atualiza o documento na memória

    talhao[campo] =
        valorNovo;


    elemento.classList.add(
        "campo-corrigido"
    );


    totalCorrecoes++;


    atualizarContadorCorrecoes();


    try {

        const resposta =
            await fetch(
                "/api/corrigir",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({

                        documento_id:
                            documentoIdAtual,

                        campo:
                            campo,

                        valor_original:
                            valorAnterior,

                        valor_corrigido:
                            valorNovo,

                        contexto:
                            JSON.stringify({

                                bloco:
                                    bloco.bloco ||
                                    bloco.codigo ||
                                    "",

                                talhao:
                                    valorNovo ||
                                    talhao.talhao ||
                                    "",

                                indiceBloco:
                                    indiceBloco,

                                indiceTalhao:
                                    indiceTalhao

                            })

                    })

                }
            );


        const dados =
            await resposta.json();


        if (
            !resposta.ok ||
            !dados.sucesso
        ) {

            throw new Error(
                dados.erro ||
                "Não foi possível salvar a correção."
            );

        }


    } catch (erro) {

        console.error(
            erro
        );


        // Mantém a alteração visual,
        // mas informa que não foi salva.

        elemento.classList.add(
            "campo-erro"
        );


        alert(
            "A alteração foi feita na tela, "
            + "mas não foi possível registrar "
            + "a correção no servidor."
        );

    }

}


// ============================================================
// ATUALIZAR CONTADOR
// ============================================================

function atualizarContadorCorrecoes() {

    const elemento =
        document.getElementById(
            "totalCorrecoes"
        );


    if (elemento) {

        elemento.textContent =
            totalCorrecoes;

    }

}


// ============================================================
// CONFIRMAR BLOCO
// ============================================================

async function confirmarBloco(
    indiceBloco
) {

    if (
        !documentoAtual ||
        !documentoAtual.blocos
    ) {

        return;

    }


    const bloco =
        documentoAtual.blocos[
            indiceBloco
        ];


    if (!bloco) {

        return;

    }


    try {

        const resposta =
            await fetch(
                "/api/confirmar",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({

                        documento_id:
                            documentoIdAtual,

                        documento:
                            documentoAtual

                    })

                }
            );


        const dados =
            await resposta.json();


        if (
            !resposta.ok ||
            !dados.sucesso
        ) {

            throw new Error(
                dados.erro ||
                "Não foi possível confirmar."
            );

        }


        alert(
            "Bloco confirmado com sucesso."
        );


        const btn =
            document.querySelector(
                `[data-bloco-index="${indiceBloco}"] .btn-confirmar-bloco`
            );


        if (btn) {

            btn.textContent =
                "✓ Bloco confirmado";

            btn.disabled =
                true;

        }


        const btnDownload =
            document.getElementById(
                "btnDownload"
            );


        if (
            btnDownload &&
            dados.download
        ) {

            btnDownload.href =
                dados.download;

        }


    } catch (erro) {

        console.error(
            erro
        );


        alert(
            "Erro ao confirmar: " +
            erro.message
        );

    }

}


// ============================================================
// ESTATÍSTICAS
// ============================================================

async function carregarEstatisticas() {

    try {

        const resposta =
            await fetch(
                "/api/estatisticas"
            );


        const dados =
            await resposta.json();


        const statDocs =
            document.getElementById(
                "statDocs"
            );


        const statExtracoes =
            document.getElementById(
                "statExtracoes"
            );


        const statCorrecoes =
            document.getElementById(
                "statCorrecoes"
            );


        if (statDocs) {

            statDocs.textContent =
                dados.documentos ?? 0;

        }


        if (statExtracoes) {

            statExtracoes.textContent =
                dados.extracoes ?? 0;

        }


        if (statCorrecoes) {

            statCorrecoes.textContent =
                dados.correcoes ?? 0;

        }

    } catch (erro) {

        console.error(
            "Erro ao carregar estatísticas:",
            erro
        );

    }

}


// ============================================================
// ESCAPAR HTML
// ============================================================

function escapeHtml(
    valor
) {

    return String(valor)

        .replace(
            /&/g,
            "&amp;"
        )

        .replace(
            /</g,
            "&lt;"
        )

        .replace(
            />/g,
            "&gt;"
        )

        .replace(
            /"/g,
            "&quot;"
        )

        .replace(
            /'/g,
            "&#039;"
        );

}


// ============================================================
// ESCAPAR ATRIBUTO
// ============================================================

function escapeAttribute(
    valor
) {

    return escapeHtml(
        valor
    );

}


// ============================================================
// INICIALIZAÇÃO
// ============================================================

carregarEstatisticas();