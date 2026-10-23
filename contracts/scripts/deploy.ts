import { ethers, network } from "hardhat";
import * as fs from "fs";
import * as path from "path";
import { abiDir, exportAbi } from "./exportar-abi";

// o índice da conta define o papel, igual ao combinado no CLAUDE.md
const ROLES = ["dono", "contratante", "transportador", "recebedor", "oraculo"];

async function main() {
  const signers = await ethers.getSigners();
  const accounts = Object.fromEntries(ROLES.map((role, i) => [role, signers[i].address]));
  const [owner, client] = signers;

  const assetTwin = await ethers.deployContract("AssetTwin", [owner.address]);
  await assetTwin.waitForDeployment();

  const escrow = await ethers.deployContract("ShipmentEscrow", [
    await assetTwin.getAddress(),
    accounts.oraculo,
  ]);
  await escrow.waitForDeployment();

  // um twin já criado para o contratante, assim dá para abrir serviço logo depois do deploy
  const metadataHash = ethers.id("claustrum:twin-inicial");
  await (await assetTwin.connect(owner).criarTwin(client.address, metadataHash)).wait();
  const firstTwinId = await assetTwin.ultimoTwinId();

  await exportAbi();

  const addresses = {
    rede: network.name,
    chainId: Number((await ethers.provider.getNetwork()).chainId),
    contratos: {
      AssetTwin: await assetTwin.getAddress(),
      ShipmentEscrow: await escrow.getAddress(),
    },
    contas: accounts,
    twinInicial: Number(firstTwinId),
  };
  const file = path.join(abiDir, "enderecos.json");
  fs.writeFileSync(file, JSON.stringify(addresses, null, 2) + "\n");
  console.log(`enderecos em ${file}`);
  console.log(JSON.stringify(addresses, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
