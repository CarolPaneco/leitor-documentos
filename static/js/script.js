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

arquivo.addEventListener("change", () => {
    if (arquivo.files.length) {
        nomeSelecionado.textContent =
            `Arquivo selecionado: ${arquivo.files[0].name}`;
    } else {
        nomeSelecionado.textContent = "";
    }
});

form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!arquivo.files.length) {
        mostrarErro("Selecione um documento antes de continuar.");
        return;
    }

    const formData = new FormData();
    formData.append("arquivo", arquivo.files[0]);

    resultado.classList.add("hidden");
    erro.classList.add("hidden");
    loading.classList.remove("hidden");

    try {
        const resposta = await fetch("/processar", {
            method: "POST",
            body: formData
        });

        const dados = await resposta.json();

        if (!resposta.ok) {
            throw new Error(dados.erro || "Erro ao processar documento.");
        }

        texto.value = dados.texto;
        nomeArquivo.textContent = dados.arquivo;
        download.href = "/download/" + dados.resultado;

        resultado.classList.remove("hidden");

    } catch (error) {
        mostrarErro(error.message);
    } finally {
        loading.classList.add("hidden");
    }
});

function mostrarErro(mensagem) {
    mensagemErro.textContent = mensagem;
    erro.classList.remove("hidden");
}
