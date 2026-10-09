// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

contract AssetTwin is ERC721, Ownable {
    // o 0 fica reservado como "nao existe" para o ShipmentEscrow, entao o primeiro id e 1
    uint256 public ultimoTwinId;

    // os metadados ficam fora da cadeia, aqui so o resumo criptografico
    mapping(uint256 => bytes32) public hashMetadados;

    event TwinCriado(uint256 indexed twinId, address indexed dono, bytes32 hashMetadados);
    event HashMetadadosAtualizado(uint256 indexed twinId, bytes32 hashAnterior, bytes32 hashNovo);

    constructor(address donoInicial)
        ERC721("Claustrum Asset Twin", "CTWIN")
        Ownable(donoInicial)
    {}

    function criarTwin(address para, bytes32 hashInicial) external returns (uint256 twinId) {
        require(msg.sender == owner(), "so o dono");
        require(para != address(0), "dono invalido");
        require(hashInicial != bytes32(0), "hash vazio");

        twinId = ++ultimoTwinId;
        // grava antes do mint porque _safeMint chama o destinatario se ele for contrato
        hashMetadados[twinId] = hashInicial;
        _safeMint(para, twinId);

        emit TwinCriado(twinId, para, hashInicial);
    }

    // so o dono do contrato, como no mint: o dono do token e o contratante e nao deve reescrever o que o servico ja usou como referencia
    function atualizarHashMetadados(uint256 twinId, bytes32 hashNovo) external {
        require(existe(twinId), "twin inexistente");
        require(msg.sender == owner(), "so o dono");
        require(hashNovo != bytes32(0), "hash vazio");

        bytes32 hashAnterior = hashMetadados[twinId];
        hashMetadados[twinId] = hashNovo;

        emit HashMetadadosAtualizado(twinId, hashAnterior, hashNovo);
    }

    function existe(uint256 twinId) public view returns (bool) {
        return _ownerOf(twinId) != address(0);
    }
}
