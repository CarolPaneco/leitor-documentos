const form = document.getElementById("uploadForm");
const arquivo = document.getElementById("arquivo");
const nomeSelecionado = document.getElementById("nomeSelecionado");
const loading = document.getElementById("loading");
const resultado = document.getElementById("resultado");
const erro = document.getElementById("erro");
const texto = document.getElementById("texto");
const nomeArquivo = document.getElementById("nomeArquivo");
const download = document.getElementById("download");
const mensagemErro = document.getElementById("mensagemErro");

// Elementos do CHECK-IN
const checkin = document.getElementById("checkin");
const checkinPercentual = document.getElementById("checkinPercentual");
const checkinResumo = document.getElementById("checkinResumo");
const camposEncontrados = document.getElementById("camposEncontrados");
const camposFaltantes = document.getElementById("camposFaltantes");
const statusCheckin = document.getElementById("statusCheckin");
const listaCheckin = document.getElementById("listaCheckin");
const alertaFaltantes = document.getElementById("alertaFaltantes");
const listaFaltantes = document.getElementById("listaFaltantes");


/* =========================================================
   SELEÇÃO DO ARQUIVO
========================================================= */

arquivo.addEventListener("change", () => {
    if (arquivo.files.length) {
        nomeSelecionado.textContent =
            `Arquivo selecionado: ${arquivo.files[0].name}`;
    } else {
        nomeSelecionado.textContent = "";
    }
});


/* =========================================================
   ENVIO DO FORMULÁRIO
========================================================= */

form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!arquivo.files.length) {
        mostrarErro("Selecione um documento antes de continuar.");
        return;
    }

    const formData = new FormData();
    formData.append("arquivo", arquivo.files[0]);

    // Esconde resultados anteriores
    resultado.classList.add("hidden");
    erro.classList.add("hidden");

    if (checkin) {
        checkin.classList.add("hidden");
    }

    // Mostra carregamento
    loading.classList.remove("hidden");

    try {

        const resposta = await fetch("/processar", {
            method: "POST",
            body: formData
        });

        const dados = await resposta.json();

        if (!resposta.ok) {
            throw new Error(
                dados.erro || "Erro ao processar documento."
            );
        }

        console.log("Resposta recebida:", dados);

        /* =====================================================
           RESULTADO DO OCR
        ===================================================== */

        texto.value = dados.texto || "";

        nomeArquivo.textContent = dados.arquivo || "";

        if (dados.resultado) {
            download.href = "/download/" + dados.resultado;
        }

        resultado.classList.remove("hidden");


        /* =====================================================
           CHECK-IN
        ===================================================== */

        if (dados.checkin) {
            mostrarCheckin(dados.checkin);
        } else {
            console.warn(
                "O servidor não retornou o objeto 'checkin'."
            );
        }

    } catch (error) {

        console.error("Erro:", error);

        mostrarErro(error.message);

    } finally {

        loading.classList.add("hidden");
    }
});


/* =========================================================
   MOSTRAR CHECK-IN
========================================================= */

function mostrarCheckin(dados) {

    if (!checkin) {
        console.warn(
            "Elemento #checkin não encontrado no HTML."
        );
        return;
    }

    console.log("Check-in recebido:", dados);

    // Exibe o bloco
    checkin.classList.remove("hidden");


    /* =====================================================
       RESUMO
    ===================================================== */

    const resumo = dados.resumo || {};

    const total = resumo.total_campos || 6;

    const encontrados =
        resumo.campos_encontrados !== undefined
            ? resumo.campos_encontrados
            : 0;

    const faltantes =
        resumo.campos_faltantes !== undefined
            ? resumo.campos_faltantes
            : 0;

    const percentual =
        resumo.percentual !== undefined
            ? resumo.percentual
            : 0;


    // Percentual
    if (checkinPercentual) {
        checkinPercentual.textContent =
            `${percentual}%`;
    }


    // Resumo
    if (checkinResumo) {
        checkinResumo.textContent =
            `${encontrados} de ${total} campos encontrados`;
    }


    // Quantidade encontrados
    if (camposEncontrados) {
        camposEncontrados.textContent =
            encontrados;
    }


    // Quantidade faltantes
    if (camposFaltantes) {
        camposFaltantes.textContent =
            faltantes;
    }


    /* =====================================================
       STATUS GERAL
    ===================================================== */

    if (statusCheckin) {

        statusCheckin.classList.remove(
            "sucesso",
            "atencao",
            "erro"
        );

        if (resumo.completo === true) {

            statusCheckin.textContent =
                "✓ Documento completo";

            statusCheckin.classList.add("sucesso");

        } else if (encontrados > 0) {

            statusCheckin.textContent =
                "⚠ Documento parcialmente identificado";

            statusCheckin.classList.add("atencao");

        } else {

            statusCheckin.textContent =
                "✕ Nenhum campo obrigatório identificado";

            statusCheckin.classList.add("erro");
        }
    }


    /* =====================================================
       LISTA DOS CAMPOS
    ===================================================== */

    if (listaCheckin) {

        listaCheckin.innerHTML = "";

        const campos = dados.campos || {};

        Object.keys(campos).forEach((nomeCampo) => {

            const campo = campos[nomeCampo];

            const encontrado =
                campo &&
                campo.encontrado === true;

            const valor =
                campo &&
                campo.valor
                    ? campo.valor
                    : null;

            const item = document.createElement("div");

            item.className =
                encontrado
                    ? "checkin-item encontrado"
                    : "checkin-item faltante";


            const icone =
                encontrado
                    ? "✓"
                    : "✕";


            let valorHTML = "";

            if (encontrado && valor) {

                valorHTML = `
                    <div class="checkin-valor">
                        ${escaparHTML(valor)}
                    </div>
                `;

            } else {

                valorHTML = `
                    <div class="checkin-valor vazio">
                        Não identificado
                    </div>
                `;
            }


            item.innerHTML = `
                <div class="checkin-icone">
                    ${icone}
                </div>

                <div class="checkin-conteudo">

                    <div class="checkin-nome">
                        ${escaparHTML(formatarNomeCampo(nomeCampo))}
                    </div>

                    ${valorHTML}

                </div>
            `;

            listaCheckin.appendChild(item);
        });
    }


    /* =====================================================
       CAMPOS FALTANTES
    ===================================================== */

    if (alertaFaltantes && listaFaltantes) {

        const lista =
            resumo.faltantes || [];

        listaFaltantes.innerHTML = "";

        if (lista.length > 0) {

            alertaFaltantes.classList.remove("hidden");

            lista.forEach((campo) => {

                const li =
                    document.createElement("li");

                li.textContent =
                    formatarNomeCampo(campo);

                listaFaltantes.appendChild(li);
            });

        } else {

            alertaFaltantes.classList.add("hidden");
        }
    }
}


/* =========================================================
   FORMATAR NOME DO CAMPO
========================================================= */

function formatarNomeCampo(nome) {

    const nomes = {

        bloco: "Bloco",

        talhao: "Talhão",

        talhoes: "Talhões",

        variedade: "Variedade",

        variedades: "Variedade",

        area: "Área",

        areas: "Área",

        plantio: "Plantio",

        plantios: "Plantio",

        propriedade: "Propriedade"
    };

    const chave =
        String(nome)
            .toLowerCase()
            .trim();

    return nomes[chave] || nome;
}


/* =========================================================
   ESCAPAR HTML
========================================================= */

function escaparHTML(valor) {

    if (valor === null || valor === undefined) {
        return "";
    }

    return String(valor)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


/* =========================================================
   MOSTRAR ERRO
========================================================= */

function mostrarErro(mensagem) {

    mensagemErro.textContent =
        mensagem;

    erro.classList.remove("hidden");
}