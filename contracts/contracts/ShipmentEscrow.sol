// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

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
    ) external payable returns (uint256 servicoId) {}

    function aceitarServico(uint256 servicoId) external {}

    function registrarCheckpoint(
        uint256 servicoId,
        bytes32 hashLeitura,
        int16 temperatura,
        uint8 umidade,
        bool lacreAberto,
        uint64 timestampLeitura
    ) external apenasOraculo {}

    function autorizarJanelaLacre(
        uint256 servicoId,
        uint64 inicio,
        uint64 fim
    ) external {}

    function confirmarEntrega(uint256 servicoId) external {}

    function multaDe(uint256 valor, uint256 segundos) public pure returns (uint256) {}

    function verServico(uint256 servicoId) external view returns (Servico memory) {}
}
