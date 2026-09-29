const arquivoInput =
    document.getElementById(
        "arquivo"
    );


const dropzone =
    document.getElementById(
        "dropzone"
    );


const btnProcessar =
    document.getElementById(
        "btnProcessar"
    );


const arquivoSelecionado =
    document.getElementById(
        "arquivoSelecionado"
    );


const loading =
    document.getElementById(
        "loading"
    );


const resultado =
    document.getElementById(
        "resultado"
    );


const mensagem =
    document.getElementById(
        "mensagem"
    );


const progressoTexto =
    document.getElementById(
        "progressoTexto"
    );


const progressoDetalhes =
    document.getElementById(
        "progressoDetalhes"
    );


const barraProgresso =
    document.getElementById(
        "barraProgresso"
    );


let arquivosAtuais = [];


// ==========================================================
// SELEÇÃO DE ARQUIVOS
// ==========================================================

dropzone.addEventListener(
    "click",
    () => {

        arquivoInput.click();

    }
);


// ==========================================================
// INPUT
// ==========================================================

arquivoInput.addEventListener(
    "change",
    () => {

        arquivosAtuais =
            Array.from(
                arquivoInput.files
            );


        mostrarArquivos();

    }
);


// ==========================================================
// DRAG OVER
// ==========================================================

dropzone.addEventListener(
    "dragover",
    event => {

        event.preventDefault();


        dropzone.classList.add(
            "dragging"
        );

    }
);


// ==========================================================
// DRAG LEAVE
// ==========================================================

dropzone.addEventListener(
    "dragleave",
    () => {

        dropzone.classList.remove(
            "dragging"
        );

    }
);


// ==========================================================
// DROP
// ==========================================================

dropzone.addEventListener(
    "drop",
    event => {

        event.preventDefault();


        dropzone.classList.remove(
            "dragging"
        );


        arquivosAtuais =
            Array.from(
                event.dataTransfer.files
            );


        mostrarArquivos();

    }
);


// ==========================================================
// MOSTRAR ARQUIVOS
// ==========================================================

function mostrarArquivos() {

    if (
        !arquivosAtuais.length
    ) {

        arquivoSelecionado.innerHTML =
            "";

        return;

    }


    const lista =
        arquivosAtuais
            .map(
                (arquivo, indice) => {

                    const tamanho =
                        formatarTamanho(
                            arquivo.size
                        );


                    return `
                        <div
                            class="arquivo-item"
                        >

                            <span
                                class="arquivo-icone"
                            >
                                📄
                            </span>


                            <span
                                class="arquivo-numero"
                            >
                                ${indice + 1}.
                            </span>


                            <span
                                class="arquivo-nome"
                            >
                                ${escapeHtml(
                                    arquivo.name
                                )}
                            </span>


                            <span
                                class="arquivo-tamanho"
                            >
                                ${tamanho}
                            </span>

                        </div>
                    `;

                }
            )
            .join("");


    arquivoSelecionado.innerHTML =
        `
        <div
            class="arquivos-resumo"
        >

            <strong>

                ${arquivosAtuais.length}

                arquivo(s)
                selecionado(s)

            </strong>


            <div
                class="lista-arquivos"
            >

                ${lista}

            </div>

        </div>
        `;

}


// ==========================================================
// BOTÃO PROCESSAR
// ==========================================================

btnProcessar.addEventListener(
    "click",
    processar
);


// ==========================================================
// PROCESSAMENTO PRINCIPAL
// ==========================================================

async function processar() {

    if (
        !arquivosAtuais.length
    ) {

        alert(
            "Selecione pelo menos um documento."
        );

        return;

    }


    // ------------------------------------------------------
    // RESET DA TELA
    // ------------------------------------------------------

    resultado.classList.add(
        "hidden"
    );


    mensagem.innerHTML =
        "";


    loading.classList.remove(
        "hidden"
    );


    btnProcessar.disabled =
        true;


    barraProgresso.style.width =
        "0%";


    progressoTexto.textContent =
        "Preparando arquivos...";


    progressoDetalhes.textContent =
        `0 de ${arquivosAtuais.length} arquivos`;


    // ------------------------------------------------------
    // RESULTADOS
    // ------------------------------------------------------

    const todasLinhas = [];

    const arquivosProcessados = [];

    const erros = [];


    // ------------------------------------------------------
    // PROCESSA UM ARQUIVO POR VEZ
    // ------------------------------------------------------

    try {

        for (
            let i = 0;
            i < arquivosAtuais.length;
            i++
        ) {

            const arquivo =
                arquivosAtuais[i];


            const numeroAtual =
                i + 1;


            progressoTexto.textContent =
                `Processando: ${arquivo.name}`;


            progressoDetalhes.textContent =
                `${numeroAtual} de ${arquivosAtuais.length} arquivos`;


            // ----------------------------------------------
            // PROGRESSO
            // ----------------------------------------------

            const progresso =
                (
                    i /
                    arquivosAtuais.length
                ) * 100;


            barraProgresso.style.width =
                `${progresso}%`;


            // ----------------------------------------------
            // ENVIA SOMENTE UM ARQUIVO
            // ----------------------------------------------

            const formData =
                new FormData();


            formData.append(
                "arquivo",
                arquivo
            );


            try {

                const resposta =
                    await fetch(
                        "/api/processar-arquivo",
                        {
                            method:
                                "POST",

                            body:
                                formData
                        }
                    );


                // ------------------------------------------
                // VERIFICA RESPOSTA
                // ------------------------------------------

                if (
                    !resposta.ok
                ) {

                    let erro =
                        "Erro ao processar o arquivo.";


                    try {

                        const dadosErro =
                            await resposta.json();


                        erro =
                            dadosErro.erro ||
                            erro;

                    } catch (_) {

                        // Mantém erro padrão.

                    }


                    throw new Error(
                        erro
                    );

                }


                const dados =
                    await resposta.json();


                if (
                    !dados.sucesso
                ) {

                    throw new Error(
                        dados.erro ||
                        "Não foi possível processar o arquivo."
                    );

                }


                // ------------------------------------------
                // GUARDA AS LINHAS
                // ------------------------------------------

                if (
                    Array.isArray(
                        dados.linhas
                    )
                ) {

                    todasLinhas.push(
                        ...dados.linhas
                    );

                }


                arquivosProcessados.push(
                    arquivo.name
                );


                // ------------------------------------------
                // ATUALIZA PROGRESSO
                // ------------------------------------------

                const progressoFinal =
                    (
                        numeroAtual /
                        arquivosAtuais.length
                    ) * 100;


                barraProgresso.style.width =
                    `${progressoFinal}%`;


            } catch (erroArquivo) {

                console.error(
                    `Erro no arquivo ${arquivo.name}:`,
                    erroArquivo
                );


                erros.push({

                    arquivo:
                        arquivo.name,

                    erro:
                        erroArquivo.message

                });

            }

        }


        // --------------------------------------------------
        // VERIFICA SE ALGUM DADO FOI EXTRAÍDO
        // --------------------------------------------------

        if (
            !todasLinhas.length
        ) {

            throw new Error(
                "Nenhum dado agrícola foi extraído dos arquivos."
            );

        }


        // --------------------------------------------------
        // TODOS OS PDFs TERMINARAM
        // --------------------------------------------------

        progressoTexto.textContent =
            "Gerando Excel...";


        progressoDetalhes.textContent =
            `${arquivosProcessados.length} de ${arquivosAtuais.length} arquivos processados`;


        barraProgresso.style.width =
            "100%";


        // --------------------------------------------------
        // ENVIA SOMENTE OS DADOS PARA GERAR EXCEL
        // --------------------------------------------------

        const respostaExcel =
            await fetch(
                "/api/gerar-excel",
                {

                    method:
                        "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify({
                            linhas:
                                todasLinhas
                        })

                }
            );


        if (
            !respostaExcel.ok
        ) {

            let erro =
                "Erro ao gerar o Excel.";


            try {

                const dadosErro =
                    await respostaExcel.json();


                erro =
                    dadosErro.erro ||
                    erro;

            } catch (_) {

                // Mantém mensagem padrão.

            }


            throw new Error(
                erro
            );

        }


        // --------------------------------------------------
        // RECEBE EXCEL
        // --------------------------------------------------

        const blob =
            await respostaExcel.blob();


        const url =
            window.URL.createObjectURL(
                blob
            );


        const nomeArquivo =
            obterNomeArquivo(
                respostaExcel
            );


        // --------------------------------------------------
        // DOWNLOAD AUTOMÁTICO
        // --------------------------------------------------

        const link =
            document.createElement(
                "a"
            );


        link.href =
            url;


        link.download =
            nomeArquivo;


        document.body.appendChild(
            link
        );


        link.click();


        link.remove();


        // --------------------------------------------------
        // RESULTADO
        // --------------------------------------------------

        mostrarSucesso(

            arquivosProcessados.length,

            todasLinhas.length,

            erros.length

        );


        resultado.classList.remove(
            "hidden"
        );


        const nomeDocumento =
            document.getElementById(
                "nomeDocumento"
            );


        nomeDocumento.textContent =
            `${arquivosProcessados.length} arquivo(s) processado(s) — ${todasLinhas.length} linha(s) extraída(s).`;


        // --------------------------------------------------
        // BOTÃO DOWNLOAD NOVAMENTE
        // --------------------------------------------------

        const btnDownload =
            document.getElementById(
                "btnDownload"
            );


        btnDownload.href =
            url;


        btnDownload.download =
            nomeArquivo;


        // --------------------------------------------------
        // MENSAGEM DE ERROS PARCIAIS
        // --------------------------------------------------

        if (
            erros.length
        ) {

            mostrarErros(
                erros
            );

        }


        // Não revogar imediatamente.
        // O botão "Baixar Excel" ainda utiliza a URL.

    } catch (erro) {

        console.error(
            erro
        );


        mensagem.innerHTML =
            `
            <div
                class="alerta erro"
            >

                <span
                    class="alerta-icone"
                >
                    ⚠
                </span>


                <div>

                    <strong>
                        Erro no processamento
                    </strong>


                    <p>
                        ${escapeHtml(
                            erro.message
                        )}
                    </p>

                </div>

            </div>
            `;


        resultado.classList.remove(
            "hidden"
        );

    } finally {

        loading.classList.add(
            "hidden"
        );


        btnProcessar.disabled =
            false;

    }

}


// ==========================================================
// NOME DO EXCEL
// ==========================================================

function obterNomeArquivo(
    resposta
) {

    const contentDisposition =
        resposta.headers.get(
            "Content-Disposition"
        );


    if (
        contentDisposition
    ) {

        const encontrado =
            contentDisposition.match(
                /filename="?([^"]+)"?/i
            );


        if (
            encontrado
        ) {

            return encontrado[1];

        }

    }


    return "dados_agricolas.xlsx";

}


// ==========================================================
// SUCESSO
// ==========================================================

function mostrarSucesso(
    quantidadeArquivos,
    quantidadeLinhas,
    quantidadeErros
) {

    let texto =
        `
        ${quantidadeArquivos}
        arquivo(s) processado(s)
        e
        ${quantidadeLinhas}
        linha(s) extraída(s).
        `;


    if (
        quantidadeErros > 0
    ) {

        texto +=
            `
            ${quantidadeErros}
            arquivo(s) apresentaram erro.
            `;

    }


    mensagem.innerHTML =
        `
        <div
            class="
                alerta
                ok
            "
        >

            <span
                class="alerta-icone"
            >
                ✓
            </span>


            <div>

                <strong>
                    Excel gerado com sucesso!
                </strong>


                <p>
                    ${texto}
                </p>

            </div>

        </div>
        `;

}


// ==========================================================
// ERROS PARCIAIS
// ==========================================================

function mostrarErros(
    erros
) {

    const lista =
        erros
            .map(
                item => {

                    return `
                        <div
                            class="alerta erro"
                        >

                            <span>
                                ⚠
                            </span>


                            <div>

                                <strong>
                                    ${escapeHtml(
                                        item.arquivo
                                    )}
                                </strong>


                                <p>
                                    ${escapeHtml(
                                        item.erro
                                    )}
                                </p>

                            </div>

                        </div>
                    `;

                }
            )
            .join("");


    mensagem.innerHTML +=
        `
        <div
            class="erros-parciais"
        >

            ${lista}

        </div>
        `;

}


// ==========================================================
// TAMANHO DO ARQUIVO
// ==========================================================

function formatarTamanho(
    bytes
) {

    if (
        bytes === 0
    ) {

        return "0 B";

    }


    const unidades = [
        "B",
        "KB",
        "MB",
        "GB"
    ];


    const indice =
        Math.floor(
            Math.log(bytes) /
            Math.log(1024)
        );


    const valor =
        bytes /
        Math.pow(
            1024,
            indice
        );


    return (
        valor.toFixed(
            indice === 0
                ? 0
                : 1
        )
        + " "
        + unidades[indice]
    );

}


// ==========================================================
// ESCAPE HTML
// ==========================================================

function escapeHtml(
    valor
) {

    return String(valor)

        .replaceAll(
            "&",
            "&amp;"
        )

        .replaceAll(
            "<",
            "&lt;"
        )

        .replaceAll(
            ">",
            "&gt;"
        )

        .replaceAll(
            '"',
            "&quot;"
        )

        .replaceAll(
            "'",
            "&#039;"
        );

}