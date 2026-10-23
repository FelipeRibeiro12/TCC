// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {AssetTwin} from "./AssetTwin.sol";

contract ShipmentEscrow {
    enum Estado { INEXISTENTE, ABERTO, ACEITO, EM_TRANSITO, CONCLUIDO, RESCINDIDO }

    // campos ordenados para empacotar os slots
    struct Servico {
        uint256 twinId;
        uint256 valor;
        address contratante;
        Estado estado;
        int16 tempMin;
        int16 tempMax;
        uint32 periodicidadeSeg;
        address transportador;
        uint64 prazoEntrega;
        address recebedor;
        uint64 ultimoCheckpoint;
        uint32 segundosForaFaixa;
        // 0 = temperatura não está fora da faixa neste momento
        uint64 inicioForaFaixa;
        uint32 segundosNaoObservados;
        // uma janela por serviço
        uint64 janelaLacreInicio;
        uint64 janelaLacreFim;
    }

    // 5 bps por segundo fora da faixa ou sem leitura
    uint256 public constant multaBpsPorSegundo = 5;

    AssetTwin public immutable assetTwin;
    address public immutable oraculo;

    // 0 reservado como "não existe"
    uint256 public ultimoServicoId;

    mapping(uint256 => Servico) private servicos;

    event ServicoAberto(uint256 indexed servicoId, uint256 indexed twinId, uint256 valor);
    event ServicoAceito(uint256 indexed servicoId, address transportador);
    event JanelaLacreAutorizada(uint256 indexed servicoId, uint64 inicio, uint64 fim);
    event CheckpointRegistrado(uint256 indexed servicoId, bytes32 hashLeitura, uint64 timestampLeitura);
    event ViolacaoAmbiental(uint256 indexed servicoId, uint32 segundosForaFaixa, uint256 penalidadeAcumulada);
    event ViolacaoLacre(uint256 indexed servicoId, uint64 momento);
    event TempoNaoObservado(uint256 indexed servicoId, uint32 segundos, uint256 penalidadeAcumulada);
    event ServicoRescindido(uint256 indexed servicoId, uint256 valorDevolvido);
    event ServicoConcluido(uint256 indexed servicoId, uint256 pagoTransportador, uint256 devolvidoContratante);

    modifier apenasOraculo() {
        require(msg.sender == oraculo, "apenas oraculo");
        _;
    }

    constructor(address enderecoAssetTwin, address enderecoOraculo) {
        require(enderecoAssetTwin != address(0), "twin invalido");
        require(enderecoOraculo != address(0), "oraculo invalido");

        assetTwin = AssetTwin(enderecoAssetTwin);
        oraculo = enderecoOraculo;
    }

    function abrirServico(
        uint256 twinId,
        address transportador,
        address recebedor,
        int16 tempMin,
        int16 tempMax,
        uint32 periodicidadeSeg,
        uint64 prazoEntrega
    ) external payable returns (uint256 servicoId) {
        // confere se existe antes, porque o ownerOf reverte sem mensagem clara quando o twin não existe
        require(
            assetTwin.existe(twinId) && assetTwin.ownerOf(twinId) == msg.sender,
            "apenas dono do twin"
        );
        require(msg.value > 0, "valor zero");
        require(transportador != address(0) && recebedor != address(0), "parte invalida");
        require(tempMin < tempMax, "faixa invalida");
        require(periodicidadeSeg > 0, "periodicidade zero");
        require(prazoEntrega > block.timestamp, "prazo no passado");

        servicoId = ++ultimoServicoId;
        Servico storage s = servicos[servicoId];
        s.twinId = twinId;
        s.valor = msg.value;
        s.contratante = msg.sender;
        s.estado = Estado.ABERTO;
        s.tempMin = tempMin;
        s.tempMax = tempMax;
        s.periodicidadeSeg = periodicidadeSeg;
        s.transportador = transportador;
        s.prazoEntrega = prazoEntrega;
        s.recebedor = recebedor;

        emit ServicoAberto(servicoId, twinId, msg.value);
    }

    function aceitarServico(uint256 servicoId) external {
        Servico storage s = servicos[servicoId];
        require(s.estado == Estado.ABERTO, "estado invalido");
        require(msg.sender == s.transportador, "apenas transportador");

        s.estado = Estado.ACEITO;
        // a contagem de tempo sem leitura começa no aceite, senão o primeiro intervalo ficaria de fora
        s.ultimoCheckpoint = uint64(block.timestamp);

        emit ServicoAceito(servicoId, msg.sender);
    }

    // aqui o papel é checado antes do estado porque o modifier roda antes do corpo da função
    function registrarCheckpoint(
        uint256 servicoId,
        bytes32 hashLeitura,
        int16 temperatura,
        uint8 umidade,
        bool lacreAberto,
        uint64 timestampLeitura
    ) external apenasOraculo {
        Servico storage s = servicos[servicoId];
        require(s.estado == Estado.ACEITO || s.estado == Estado.EM_TRANSITO, "estado invalido");
        require(umidade <= 100, "umidade invalida");
        // leitura repetida ou mais velha que a última não entra, mesmo se o oráculo deixar passar
        require(timestampLeitura > s.ultimoCheckpoint, "leitura antiga");
        require(timestampLeitura <= block.timestamp, "leitura no futuro");

        if (s.estado == Estado.ACEITO) {
            s.estado = Estado.EM_TRANSITO;
        }

        emit CheckpointRegistrado(servicoId, hashLeitura, timestampLeitura);

        if (lacreAberto && !dentroDaJanela(s, timestampLeitura)) {
            emit ViolacaoLacre(servicoId, timestampLeitura);
            rescindir(servicoId, s);
            return;
        }

        contarNaoObservado(servicoId, s, timestampLeitura);

        bool foraDaFaixa = temperatura < s.tempMin || temperatura > s.tempMax;
        if (foraDaFaixa && s.inicioForaFaixa == 0) {
            s.inicioForaFaixa = timestampLeitura;
        } else if (!foraDaFaixa && s.inicioForaFaixa != 0) {
            fecharForaFaixa(servicoId, s, timestampLeitura);
        }

        s.ultimoCheckpoint = timestampLeitura;
    }

    function autorizarJanelaLacre(
        uint256 servicoId,
        uint64 inicio,
        uint64 fim
    ) external {
        Servico storage s = servicos[servicoId];
        require(
            s.estado == Estado.ABERTO || s.estado == Estado.ACEITO || s.estado == Estado.EM_TRANSITO,
            "estado invalido"
        );
        require(msg.sender == s.contratante, "apenas contratante");
        require(fim > inicio, "janela invalida");

        s.janelaLacreInicio = inicio;
        s.janelaLacreFim = fim;

        emit JanelaLacreAutorizada(servicoId, inicio, fim);
    }

    function confirmarEntrega(uint256 servicoId) external {
        Servico storage s = servicos[servicoId];
        require(s.estado == Estado.ACEITO || s.estado == Estado.EM_TRANSITO, "estado invalido");
        require(msg.sender == s.recebedor, "apenas recebedor");

        // fecha o que ainda estava aberto, senão um desvio ou silêncio no fim da viagem sairia de graça
        uint64 agora = uint64(block.timestamp);
        contarNaoObservado(servicoId, s, agora);
        if (s.inicioForaFaixa != 0) {
            fecharForaFaixa(servicoId, s, agora);
        }

        uint256 valor = s.valor;
        uint256 multa = multaDe(valor, uint256(s.segundosForaFaixa) + s.segundosNaoObservados);
        uint256 pagoTransportador = valor - multa;

        // zera antes de transferir para ninguém conseguir sacar duas vezes
        s.valor = 0;
        s.estado = Estado.CONCLUIDO;

        transferir(s.transportador, pagoTransportador);
        transferir(s.contratante, multa);

        emit ServicoConcluido(servicoId, pagoTransportador, multa);
    }

    function multaDe(uint256 valor, uint256 segundos) public pure returns (uint256) {
        // com 2000 segundos a multa já chega no valor inteiro, e parar aqui evita estouro na conta
        if (segundos >= 10000 / multaBpsPorSegundo) {
            return valor;
        }
        return Math.mulDiv(valor, segundos * multaBpsPorSegundo, 10000);
    }

    function verServico(uint256 servicoId) external view returns (Servico memory) {
        return servicos[servicoId];
    }

    function dentroDaJanela(Servico storage s, uint64 momento) private view returns (bool) {
        return momento >= s.janelaLacreInicio && momento <= s.janelaLacreFim;
    }

    // conta tudo que passou da periodicidade, seja qual for o motivo do silêncio
    function contarNaoObservado(uint256 servicoId, Servico storage s, uint64 ate) private {
        uint64 intervalo = ate - s.ultimoCheckpoint;
        if (intervalo <= s.periodicidadeSeg) {
            return;
        }
        uint32 excedente = paraUint32(intervalo - s.periodicidadeSeg);
        s.segundosNaoObservados += excedente;

        emit TempoNaoObservado(servicoId, excedente, penalidadeAcumulada(s));
    }

    // o intervalo herda a condição da leitura anterior, então o desvio só fecha quando a temperatura volta
    function fecharForaFaixa(uint256 servicoId, Servico storage s, uint64 ate) private {
        uint32 duracao = paraUint32(ate - s.inicioForaFaixa);
        s.segundosForaFaixa += duracao;
        s.inicioForaFaixa = 0;

        emit ViolacaoAmbiental(servicoId, duracao, penalidadeAcumulada(s));
    }

    function rescindir(uint256 servicoId, Servico storage s) private {
        uint256 valor = s.valor;
        s.valor = 0;
        s.estado = Estado.RESCINDIDO;

        transferir(s.contratante, valor);

        emit ServicoRescindido(servicoId, valor);
    }

    function transferir(address para, uint256 quantia) private {
        if (quantia == 0) {
            return;
        }
        (bool ok, ) = para.call{value: quantia}("");
        require(ok, "transferencia falhou");
    }

    // cast direto cortaria os bits sem avisar, e a multa sairia menor do que devia
    function paraUint32(uint64 segundos) private pure returns (uint32) {
        require(segundos <= type(uint32).max, "intervalo grande demais");
        return uint32(segundos);
    }

    function penalidadeAcumulada(Servico storage s) private view returns (uint256) {
        return multaDe(s.valor, uint256(s.segundosForaFaixa) + s.segundosNaoObservados);
    }
}
